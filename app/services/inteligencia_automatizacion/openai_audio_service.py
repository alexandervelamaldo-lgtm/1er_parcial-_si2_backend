"""Transcripción de audio con OpenAI Whisper-1.

Reemplaza a Gemini para transcripción cuando AI_PROVIDER="openai".
Llama a /audio/transcriptions con language="es" para forzar español.

Garantías:
  - Nunca lanza excepción. Si Whisper falla, devolvemos `fallback=True`
    y transcript="".
  - Nunca loguea la clave ni el contenido del audio (solo nombre + tamaño).
  - Timeout configurable (default 60s, audios largos pueden tomar ~30s).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# URL por defecto (OpenAI). El endpoint real se toma de settings.openai_audio_url,
# lo que permite apuntar a un proveedor compatible y gratuito como Groq sin tocar
# código — solo variables de entorno.
OPENAI_AUDIO_URL = "https://api.openai.com/v1/audio/transcriptions"


@dataclass(slots=True)
class OpenAIAudioResult:
    transcript: str
    confidence: float  # Whisper no devuelve confidence — estimamos por longitud
    latency_ms: int
    model: str = ""
    fallback: bool = False
    error: str | None = None


def _degraded(latency_ms: int, error: str | None, model: str = "") -> OpenAIAudioResult:
    return OpenAIAudioResult(
        transcript="",
        confidence=0.0,
        latency_ms=latency_ms,
        model=model,
        fallback=True,
        error=error,
    )


async def transcribe_audio(
    *,
    audio_bytes: bytes,
    file_name: str,
    mime_type: str | None,
) -> OpenAIAudioResult:
    """Transcribe un blob de audio a texto en español.

    Args:
        audio_bytes: contenido binario del archivo.
        file_name: usado solo para que Whisper infiera el formato; no se
            loguea junto con la respuesta.
        mime_type: MIME del archivo (audio/mpeg, audio/wav, audio/m4a, etc.).
    """
    settings = get_settings()
    # Clave específica de audio si existe; si no, la de OpenAI general. Así se
    # puede usar Groq (gratis) solo para transcripción sin afectar la visión.
    api_key = (settings.openai_audio_api_key or settings.openai_api_key or "").strip()
    model = settings.openai_audio_model or "whisper-1"
    timeout = float(settings.openai_audio_timeout_s)
    url = (settings.openai_audio_url or OPENAI_AUDIO_URL).strip()

    started = time.perf_counter()

    if len(api_key) < 10:
        logger.warning("OpenAI audio: API key no configurada — fallback.")
        return _degraded(
            latency_ms=int((time.perf_counter() - started) * 1000),
            error="openai_api_key_missing",
            model=model,
        )
    if not audio_bytes:
        return _degraded(
            latency_ms=int((time.perf_counter() - started) * 1000),
            error="empty_audio",
            model=model,
        )

    # Whisper espera multipart/form-data. El nombre y mime ayudan a inferir
    # el codec, así que pasamos los originales.
    safe_name = (file_name or "audio.wav").strip() or "audio.wav"
    safe_mime = (mime_type or "audio/wav").strip() or "audio/wav"

    files = {
        "file": (safe_name, audio_bytes, safe_mime),
    }
    data = {
        "model": model,
        "language": "es",
        # response_format=verbose_json incluiría timestamps; nos basta json.
        "response_format": "json",
        "temperature": "0",
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers=headers,
                files=files,
                data=data,
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as exc:
        # El cuerpo de error de Whisper explica el motivo real (clave inválida,
        # formato no soportado, cuota agotada…). Lo logueamos para diagnóstico;
        # NUNCA logueamos la API key ni el contenido del audio.
        body_preview = ""
        try:
            body_preview = (exc.response.text or "")[:500]
        except Exception:
            pass
        logger.warning(
            "OpenAI Whisper falló — status=%s, archivo=%s, mime=%s, bytes=%d, body=%s",
            exc.response.status_code, safe_name, safe_mime, len(audio_bytes), body_preview,
        )
        return _degraded(
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=f"HTTP {exc.response.status_code}",
            model=model,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "OpenAI Whisper error de red — tipo=%s, archivo=%s, bytes=%d",
            type(exc).__name__, safe_name, len(audio_bytes),
        )
        return _degraded(
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=f"network: {type(exc).__name__}",
            model=model,
        )
    except Exception as exc:
        return _degraded(
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=f"unknown: {type(exc).__name__}",
            model=model,
        )

    transcript = ""
    if isinstance(payload, dict):
        text_field = payload.get("text")
        if isinstance(text_field, str):
            transcript = text_field.strip()

    latency_ms = int((time.perf_counter() - started) * 1000)

    if not transcript:
        return _degraded(latency_ms=latency_ms, error="empty_transcript", model=model)

    # Confianza estimada por longitud — Whisper no la expone directamente.
    # 0.82 si tiene contenido razonable; 0.5 si es muy corto.
    confidence = 0.82 if len(transcript) >= 12 else 0.55

    # Whisper-1 cuesta $0.006/minuto. No tenemos duración aquí, pero
    # con tamaño en bytes podemos estimarla muy crudo. Si quisiéramos
    # precisión, leeríamos metadata; por ahora solo logueamos latencia.
    logger.info(
        "OpenAI Whisper OK — modelo=%s, bytes=%d, latency_ms=%d, len_text=%d",
        model, len(audio_bytes), latency_ms, len(transcript),
    )

    return OpenAIAudioResult(
        transcript=transcript,
        confidence=confidence,
        latency_ms=latency_ms,
        model=model,
        fallback=False,
    )
