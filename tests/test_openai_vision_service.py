"""Tests del servicio de visión OpenAI (gpt-4o-mini) con costo.

Mockeamos las respuestas HTTP con `respx` (vía httpx). El servicio NUNCA
lanza excepciones — incluso si la API falla, devuelve `fallback=True`.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import get_settings
from app.services.inteligencia_automatizacion.openai_vision_service import (
    OPENAI_CHAT_URL,
    analyze_vehicle_image,
)


def _valid_openai_response(payload: dict, tokens_in: int = 100, tokens_out: int = 50) -> dict:
    """Estructura típica de /v1/chat/completions con content=JSON serializado."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }


def _set_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forzamos una key falsa para que el servicio no aborte al ver vacío."""
    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-FAKE-KEY-FOR-RESPX-MOCKING")


@pytest.mark.asyncio
async def test_openai_vision_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caso feliz: la IA responde JSON válido con costo en rango plausible."""
    _set_openai_key(monkeypatch)
    payload = {
        "descripcion": "Abolladura leve en el parachoques delantero del lado derecho.",
        "categoria_dano": "chaperia_pintura",
        "nivel_riesgo": "BAJO",
        "confianza_analisis": 0.82,
        "costo_estimado": {
            "moneda": "BOB",
            "minimo": 400,
            "maximo": 1500,
            "mas_probable": 800,
            "desglose": [
                {"concepto": "mano de obra", "min": 200, "max": 600},
                {"concepto": "pintura", "min": 200, "max": 900},
            ],
            "supuestos": ["asume daño superficial sin chasis afectado"],
            "confianza_costo": 0.7,
        },
    }
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_valid_openai_response(payload))
        )
        result = await analyze_vehicle_image(
            image_bytes=b"fake-jpeg-bytes-not-validated-by-mock",
            mime_type="image/jpeg",
            context="parachoques abollado",
        )
    assert result.fallback is False
    assert result.categoria_dano == "chaperia_pintura"
    assert result.nivel_riesgo == "BAJO"
    assert result.costo_estimado is not None
    assert result.costo_estimado.minimo == 400
    assert result.costo_estimado.maximo == 1500
    assert result.costo_estimado.mas_probable == 800
    assert result.costo_estimado.confianza_costo == pytest.approx(0.7)
    assert len(result.costo_estimado.desglose) == 2
    # Tokens y model deben quedar en métricas.
    assert result.tokens_in == 100
    assert result.tokens_out == 50
    assert result.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_openai_vision_invalid_json_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si la primera respuesta tiene contradicción interna (max<min), se
    descarta y se reintenta con temperature=0. Si el retry sí es válido,
    el caller recibe éxito."""
    _set_openai_key(monkeypatch)
    bad = {
        "descripcion": "respuesta inconsistente",
        "categoria_dano": "general",
        "nivel_riesgo": "MEDIO",
        "confianza_analisis": 0.5,
        "costo_estimado": {
            "moneda": "BOB",
            # Contradicción: minimo > maximo → _parse_payload retorna None.
            "minimo": 9000,
            "maximo": 200,
            "mas_probable": 500,
            "desglose": [],
            "supuestos": [],
            "confianza_costo": 0.3,
        },
    }
    good = {
        "descripcion": "Pinchazo en la llanta delantera izquierda.",
        "categoria_dano": "pinchazo",
        "nivel_riesgo": "BAJO",
        "confianza_analisis": 0.9,
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
        route = router.post("/v1/chat/completions").mock(
            side_effect=[
                httpx.Response(200, json=_valid_openai_response(bad)),
                httpx.Response(200, json=_valid_openai_response(good, tokens_in=80, tokens_out=40)),
            ]
        )
        result = await analyze_vehicle_image(
            image_bytes=b"fake",
            mime_type="image/jpeg",
            context="llanta",
        )
    assert route.call_count == 2  # confirmamos que hubo retry
    assert result.fallback is False
    assert result.categoria_dano == "pinchazo"
    assert result.costo_estimado is not None
    assert result.costo_estimado.mas_probable == 50


@pytest.mark.asyncio
async def test_openai_vision_complete_failure_returns_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si OpenAI devuelve 500 dos veces, NO se propaga excepción — el
    resultado viene con fallback=True y costo_estimado=None."""
    _set_openai_key(monkeypatch)
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        result = await analyze_vehicle_image(
            image_bytes=b"fake",
            mime_type="image/jpeg",
            context="cualquiera",
        )
    assert result.fallback is True
    assert result.costo_estimado is None
    # El call site verifica este flag para marcar requiere_revision_humana.
    assert result.descripcion == ""
    assert result.categoria_dano == "general"  # default seguro


@pytest.mark.asyncio
async def test_openai_vision_low_confidence_abstention(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cuando la IA devuelve confianza_costo<0.4 con costo_estimado=null
    (abstención explícita), el resultado es válido (fallback=False) pero
    sin costo. El caller debe marcar requiere_revision_humana."""
    _set_openai_key(monkeypatch)
    payload = {
        "descripcion": "Imagen demasiado borrosa para estimar.",
        "categoria_dano": "general",
        "nivel_riesgo": "MEDIO",
        "confianza_analisis": 0.15,
        "costo_estimado": None,
    }
    async with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_valid_openai_response(payload))
        )
        result = await analyze_vehicle_image(
            image_bytes=b"fake",
            mime_type="image/jpeg",
            context="",
        )
    assert result.fallback is False
    assert result.costo_estimado is None
    assert result.confianza_analisis < 0.3  # caller usa esto para revision_manual


@pytest.mark.asyncio
async def test_openai_vision_no_api_key_returns_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin OPENAI_API_KEY, el servicio NO intenta llamar — fallback inmediato."""
    get_settings.cache_clear()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    result = await analyze_vehicle_image(
        image_bytes=b"fake",
        mime_type="image/jpeg",
        context="",
    )
    assert result.fallback is True
    assert result.error == "openai_api_key_missing"
