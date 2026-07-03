"""Tests del servicio de transcripción Whisper de OpenAI.

Mockeamos /v1/audio/transcriptions con respx. El servicio NUNCA lanza
excepciones.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import get_settings
from app.services.inteligencia_automatizacion.openai_audio_service import (
    OPENAI_AUDIO_URL,
    transcribe_audio,
)


def _set_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-FAKE-KEY-FOR-RESPX")
    # Pin the audio endpoint/key to OpenAI so these respx-mocked tests stay
    # isolated from the real backend/.env (which may point OPENAI_AUDIO_URL at
    # Groq for production). Without this the service would call Groq and respx
    # would report "some routes were not called".
    monkeypatch.setenv("OPENAI_AUDIO_URL", "https://api.openai.com/v1/audio/transcriptions")
    monkeypatch.setenv("OPENAI_AUDIO_API_KEY", "")
    monkeypatch.setenv("OPENAI_AUDIO_MODEL", "whisper-1")


@pytest.mark.asyncio
async def test_openai_audio_transcripcion_espanol(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caso feliz: Whisper devuelve texto en español."""
    _set_openai_key(monkeypatch)
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/audio/transcriptions").mock(
            return_value=httpx.Response(
                200,
                json={"text": "Se me pinchó la llanta delantera derecha cerca del aeropuerto."},
            )
        )
        result = await transcribe_audio(
            audio_bytes=b"fake-audio-blob",
            file_name="reporte.m4a",
            mime_type="audio/m4a",
        )
    assert result.fallback is False
    assert "pinch" in result.transcript.lower()
    assert result.confidence > 0.5
    assert result.model == "whisper-1"


@pytest.mark.asyncio
async def test_openai_audio_empty_transcript_marks_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si Whisper devuelve text="" → tratamos como fallback (audio inútil)."""
    _set_openai_key(monkeypatch)
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": ""})
        )
        result = await transcribe_audio(
            audio_bytes=b"fake",
            file_name="audio.wav",
            mime_type="audio/wav",
        )
    assert result.fallback is True
    assert result.transcript == ""
    assert result.error == "empty_transcript"


@pytest.mark.asyncio
async def test_openai_audio_http_error_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx de Whisper → fallback, no excepción."""
    _set_openai_key(monkeypatch)
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/audio/transcriptions").mock(
            return_value=httpx.Response(503, json={"error": "downstream"})
        )
        result = await transcribe_audio(
            audio_bytes=b"fake",
            file_name="audio.wav",
            mime_type="audio/wav",
        )
    assert result.fallback is True
    assert result.error and "HTTP 503" in result.error
