"""Tests de integración del bridge OpenAI dentro de multimodal_ai_service.

Validamos que cuando AI_PROVIDER="openai", `analyze_image_file` y
`transcribe_audio_file` delegan correctamente al servicio OpenAI y los
campos nuevos (costo_min_bob, costo_probable_bob, requiere_revision_humana,
etc.) se propagan al ImageAnalysisResult.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import get_settings
from app.services.inteligencia_automatizacion.multimodal_ai_service import (
    analyze_image_file,
    transcribe_audio_file,
)


def _valid_openai_chat(payload: dict) -> dict:
    return {
        "id": "chatcmpl-test",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(payload)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
    }


def _force_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-FAKE-FOR-RESPX")
    monkeypatch.setenv("IA_COSTO_HABILITADO", "true")
    # Pin the audio endpoint/key to OpenAI so the respx-mocked transcription
    # tests stay isolated from the real backend/.env (which may point
    # OPENAI_AUDIO_URL at Groq in production).
    monkeypatch.setenv("OPENAI_AUDIO_URL", "https://api.openai.com/v1/audio/transcriptions")
    monkeypatch.setenv("OPENAI_AUDIO_API_KEY", "")
    monkeypatch.setenv("OPENAI_AUDIO_MODEL", "whisper-1")


@pytest.mark.asyncio
async def test_analyze_image_file_returns_cost_when_provider_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cuando AI_PROVIDER=openai, el resultado incluye costo_min_bob/max/probable
    y propaga ia_model='gpt-4o-mini'."""
    _force_openai_provider(monkeypatch)
    payload = {
        "descripcion": "Pinchazo en llanta trasera derecha.",
        "categoria_dano": "pinchazo",
        "nivel_riesgo": "BAJO",
        "confianza_analisis": 0.88,
        "costo_estimado": {
            "moneda": "BOB",
            "minimo": 30,
            "maximo": 80,
            "mas_probable": 50,
            "desglose": [{"concepto": "parche", "min": 30, "max": 80}],
            "supuestos": ["asume llanta reparable"],
            "confianza_costo": 0.85,
        },
    }
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_valid_openai_chat(payload))
        )
        result = await analyze_image_file(
            "foto.jpg",
            "image/jpeg",
            "llanta pinchada en la carretera",
            file_bytes=b"fake-jpeg-bytes",
        )
    assert result.provider == "openai"
    assert result.costo_probable_bob == 50
    assert result.costo_min_bob == 30
    assert result.costo_max_bob == 80
    assert result.costo_confianza == pytest.approx(0.85)
    assert result.categoria_dano_ia == "pinchazo"
    assert result.nivel_riesgo_ia == "BAJO"
    assert result.requiere_revision_humana is False
    assert result.ia_model == "gpt-4o-mini"
    # Severity legacy se infiere de nivel_riesgo
    assert result.severity == "LEVE"


@pytest.mark.asyncio
async def test_analyze_image_file_marks_revision_when_low_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si la IA devuelve confianza_costo < 0.4, requiere_revision_humana=True."""
    _force_openai_provider(monkeypatch)
    payload = {
        "descripcion": "Daño visible pero alcance incierto.",
        "categoria_dano": "general",
        "nivel_riesgo": "MEDIO",
        "confianza_analisis": 0.5,
        "costo_estimado": {
            "moneda": "BOB",
            "minimo": 0,
            "maximo": 3500,
            "mas_probable": 1200,
            "desglose": [],
            "supuestos": ["imagen parcial"],
            "confianza_costo": 0.25,  # < 0.4 → revisión humana
        },
    }
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_valid_openai_chat(payload))
        )
        result = await analyze_image_file(
            "foto.jpg", "image/jpeg", "", file_bytes=b"x",
        )
    assert result.requiere_revision_humana is True
    assert result.costo_confianza == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_analyze_image_file_falls_back_when_openai_completely_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si OpenAI devuelve 500 dos veces, NO se propaga 500 al caller — el
    flujo sigue con provider='openai-fallback', requiere_revision_humana=True
    y sin costo."""
    _force_openai_provider(monkeypatch)
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        result = await analyze_image_file(
            "foto.jpg", "image/jpeg", "", file_bytes=b"x",
        )
    assert result.provider == "openai-fallback"
    assert result.requiere_revision_humana is True
    assert result.costo_probable_bob is None
    assert result.ia_fallback is True


@pytest.mark.asyncio
async def test_transcribe_audio_file_uses_openai_when_provider_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_openai_provider(monkeypatch)
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/audio/transcriptions").mock(
            return_value=httpx.Response(
                200,
                json={"text": "Se me pinchó la llanta cerca del aeropuerto."},
            )
        )
        result = await transcribe_audio_file(
            "audio.m4a", "audio/m4a", 1024, file_bytes=b"audio-bytes",
        )
    assert result.provider == "openai"
    assert "pinch" in result.transcript.lower()
    assert result.confidence > 0.5


@pytest.mark.asyncio
async def test_transcribe_audio_file_no_fabrica_texto_si_whisper_falla(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug 2: si Whisper falla (p. ej. 401 clave inválida) y no hay otro
    proveedor real, NO devolvemos texto inventado. El resultado debe ser
    honesto: vacío + requiere_revision_humana=True."""
    _force_openai_provider(monkeypatch)
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/audio/transcriptions").mock(
            return_value=httpx.Response(401, json={"error": {"message": "invalid key"}})
        )
        result = await transcribe_audio_file(
            "audio.m4a", "audio/m4a", 1024, file_bytes=b"audio-bytes",
        )
    assert result.provider == "unavailable"
    assert result.transcript == ""
    assert result.requiere_revision_humana is True


@pytest.mark.asyncio
async def test_transcription_unavailable_when_no_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si ningún proveedor real está configurado, NO se fabrica transcripción.
    El resultado es honesto: transcript vacío, provider='unavailable' y
    requiere_revision_humana=True para que el caller marque estado ERROR."""
    get_settings.cache_clear()
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    result = await transcribe_audio_file(
        "audio_bateria_motor.wav", "audio/wav", 512,
    )
    assert result.provider == "unavailable"
    assert result.transcript == ""
    assert result.requiere_revision_humana is True
