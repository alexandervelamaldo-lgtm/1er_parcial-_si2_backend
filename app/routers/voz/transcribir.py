"""Voice transcription endpoint — uses OpenAI Whisper server-side.

The API key is read exclusively from server settings and is never
returned to any client. All clients receive only the resulting text.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status

import httpx

from app.config import get_settings
from app.dependencies.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voz", tags=["Voz"])

# OpenAI Whisper hard limits
_MAX_BYTES = 25 * 1024 * 1024  # 25 MB
_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
_ALLOWED_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".flac"}


@router.post(
    "/transcribir",
    summary="Transcribe an audio clip to text using OpenAI Whisper",
    response_description="Transcribed text from the audio clip",
)
async def transcribir_audio(
    audio: UploadFile = File(..., description="Audio file (m4a, mp3, wav, webm — max 25 MB)"),
    _current_user=Depends(get_current_user),
) -> dict[str, str]:
    """Accept an audio upload and return its Spanish transcription.

    - Requires a valid JWT (any authenticated role).
    - The OpenAI API key is read from `OPENAI_API_KEY` env var; if absent
      the endpoint returns 503 so the app can degrade gracefully.
    - Audio is streamed directly to Whisper — it is NOT stored on disk.
    """
    settings = get_settings()
    # Clave específica de audio si existe; si no, la de OpenAI general. Permite
    # usar un proveedor compatible y GRATIS (p. ej. Groq) solo para
    # transcripción, sin tocar la configuración de visión. La clave nunca se
    # devuelve al cliente.
    api_key = (settings.openai_audio_api_key or settings.openai_api_key or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La transcripción de voz no está configurada en el servidor.",
        )

    # ── Validate file ─────────────────────────────────────────────────────────
    filename = audio.filename or "audio.m4a"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Formato no soportado ({ext}). Usa m4a, mp3, wav o webm.",
        )

    data = await audio.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El audio supera el límite de 25 MB de Whisper.",
        )
    if len(data) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo de audio está vacío.")

    # ── Call Whisper ──────────────────────────────────────────────────────────
    content_type = audio.content_type or "audio/m4a"
    # Endpoint y modelo configurables: por defecto OpenAI Whisper, pero pueden
    # apuntar a un proveedor compatible y gratuito (Groq:
    # https://api.groq.com/openai/v1/audio/transcriptions, whisper-large-v3-turbo).
    whisper_url = (settings.openai_audio_url or _WHISPER_URL).strip()
    whisper_model = settings.openai_audio_model or "whisper-1"
    try:
        async with httpx.AsyncClient(timeout=float(settings.openai_audio_timeout_s)) as client:
            response = await client.post(
                whisper_url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (filename, data, content_type)},
                data={"model": whisper_model, "language": "es"},
            )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="El servicio de transcripción tardó demasiado. Intenta con un audio más corto.",
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo contactar el servicio de transcripción: {exc}",
        )

    if response.status_code != 200:
        # Log diagnóstico: el cuerpo de error de Whisper trae el motivo real
        # (clave inválida, formato no soportado, cuota agotada, etc.). NUNCA
        # logueamos la API key ni el audio — solo status + cuerpo de la API.
        body_preview = (response.text or "")[:500]
        logger.warning(
            "Whisper /voz/transcribir falló — status=%s, archivo=%s, content_type=%s, bytes=%d, body=%s",
            response.status_code, filename, content_type, len(data), body_preview,
        )
        if response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Clave de API de transcripción inválida. Contacta al administrador.",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al transcribir ({response.status_code}). Intenta de nuevo.",
        )

    resultado = response.json()
    texto = (resultado.get("text") or "").strip()
    return {"texto": texto}
