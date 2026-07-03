"""Chat conversacional con Groq (modelos Llama servidos por Groq).

Groq expone una API compatible con el formato de OpenAI Chat Completions,
así que reutilizamos el mismo patrón httpx que openai_audio_service /
openai_vision_service en vez de sumar el SDK oficial como dependencia
nueva.

Garantías:
  - Nunca lanza excepción. Si Groq falla, devolvemos `ok=False` y un
    `error`/`status_code` descriptivos; el router decide qué
    HTTPException emitir (401 → clave inválida, 429 → límite de tasa).
  - Nunca loguea la clave ni el contenido de los mensajes del usuario
    (solo cantidad de mensajes y longitud de la respuesta).
  - Timeout configurable (default 15s).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Límite de mensajes de historial que se reenvían en cada solicitud. Evita
# prompts gigantes (costo y latencia) sin perder demasiado contexto.
MAX_HISTORIAL_MENSAJES = 20


@dataclass(slots=True)
class GroqChatResult:
    ok: bool
    reply: str
    latency_ms: int
    model: str = ""
    status_code: int | None = None
    error: str | None = None


def _degraded(
    latency_ms: int,
    error: str,
    model: str,
    status_code: int | None = None,
) -> GroqChatResult:
    return GroqChatResult(
        ok=False,
        reply="",
        latency_ms=latency_ms,
        model=model,
        status_code=status_code,
        error=error,
    )


def _extraer_respuesta(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


async def enviar_chat(*, mensajes: list[dict[str, str]]) -> GroqChatResult:
    """Envía una conversación (historial + mensaje nuevo) a Groq.

    Args:
        mensajes: lista de dicts ``{"role": "system"|"user"|"assistant",
            "content": str}`` en el orden de la conversación. Se trunca
            automáticamente a los últimos ``MAX_HISTORIAL_MENSAJES``.

    Returns:
        GroqChatResult: ``ok=True`` con ``reply`` si Groq respondió
        correctamente, o ``ok=False`` con ``error``/``status_code`` si
        falló (clave faltante, rate limit, error de red, etc.).
    """
    settings = get_settings()
    api_key = (settings.groq_api_key or "").strip()
    model = settings.groq_chat_model or "llama-3.3-70b-versatile"
    timeout = float(settings.groq_timeout_s)

    started = time.perf_counter()

    if len(api_key) < 10:
        logger.warning("Groq chat: API key no configurada — fallback.")
        return _degraded(
            int((time.perf_counter() - started) * 1000),
            "groq_api_key_missing",
            model,
        )
    if not mensajes:
        return _degraded(
            int((time.perf_counter() - started) * 1000),
            "empty_messages",
            model,
        )

    payload = {
        "model": model,
        "messages": mensajes[-MAX_HISTORIAL_MENSAJES:],
        "temperature": 0.4,
        "max_tokens": 700,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(GROQ_CHAT_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        body_preview = ""
        try:
            body_preview = (exc.response.text or "")[:500]
        except Exception:
            pass
        logger.warning(
            "Groq chat falló — status=%s, body=%s",
            status_code, body_preview,
        )
        return _degraded(
            int((time.perf_counter() - started) * 1000),
            f"HTTP {status_code}",
            model,
            status_code,
        )
    except httpx.HTTPError as exc:
        logger.warning("Groq chat error de red — tipo=%s", type(exc).__name__)
        return _degraded(
            int((time.perf_counter() - started) * 1000),
            f"network: {type(exc).__name__}",
            model,
        )
    except Exception as exc:
        return _degraded(
            int((time.perf_counter() - started) * 1000),
            f"unknown: {type(exc).__name__}",
            model,
        )

    reply = _extraer_respuesta(data if isinstance(data, dict) else {})
    latency_ms = int((time.perf_counter() - started) * 1000)

    if not reply:
        return _degraded(latency_ms, "empty_reply", model)

    logger.info(
        "Groq chat OK — modelo=%s, latency_ms=%d, len_reply=%d",
        model, latency_ms, len(reply),
    )
    return GroqChatResult(ok=True, reply=reply, latency_ms=latency_ms, model=model)
