"""Servicio de visión con OpenAI gpt-4o-mini.

Reemplaza a Gemini cuando AI_PROVIDER="openai". En una sola llamada al modelo
obtenemos:
  - descripción y categoría del daño,
  - nivel de riesgo y confianza del análisis,
  - estimación de costo de reparación en BOB con desglose y supuestos.

Diseño anti-alucinación:
  - response_format con JSON Schema estricto.
  - system prompt con tabla de referencia de precios en Santa Cruz, Bolivia.
  - temperature=0.2 (algo de variabilidad ayuda en estimaciones, no tanta
    como para inventar cifras).
  - Si confianza_costo < 0.4 → el modelo está instruido a devolver rango
    amplio y costo_estimado.confianza_costo < 0.4 (preferimos abstenernos
    a inventar números).
  - Retry una vez con temperature=0 si el JSON viene inválido.
  - Fallback degradado si todo falla — NUNCA propagamos la excepción.

La clave OPENAI_API_KEY se lee de settings (que la lee de backend/.env).
Nunca se loguea ni se expone al cliente.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Tabla de referencia para Santa Cruz, Bolivia. Va en el system prompt para
# anclar al modelo a precios reales del mercado local y evitar que invente.
PRICE_REFERENCE_BOB = """
Rangos típicos de reparación en BOB (Santa Cruz, Bolivia) — usa estos
anclajes como guía, no como verdad absoluta:
- Cambio de llanta:              150 - 400 BOB
- Pinchazo / parche:              30 -  80 BOB
- Batería nueva (sedán):         600 - 1200 BOB
- Chapería abolladura menor:     400 - 1500 BOB
- Pintura panel completo:       1200 - 3500 BOB
- Cambio de amortiguador:        600 - 1800 BOB por unidad
- Diagnóstico eléctrico:         150 -  400 BOB
- Cambio de pastillas de freno:  300 -  800 BOB
- Reparación de motor (mayor):  3000 - 12000 BOB
""".strip()

CATEGORIAS_VALIDAS = [
    "chaperia_pintura",
    "motor",
    "dano_electrico",
    "pinchazo",
    "falla_mecanica",
    "suspension",
    "general",
]

NIVELES_RIESGO = ["BAJO", "MEDIO", "ALTO", "CRITICO"]

# JSON Schema estricto que OpenAI debe respetar. Si el modelo devuelve algo
# que no cumple, la API misma rechaza la respuesta — esto es nuestro primer
# muro anti-alucinación de forma.
VISION_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "AnalisisDanoVehicular",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "descripcion",
            "categoria_dano",
            "nivel_riesgo",
            "confianza_analisis",
            "costo_estimado",
        ],
        "properties": {
            "descripcion": {
                "type": "string",
                "description": "Descripción del daño en 1-3 frases, español neutro.",
            },
            "categoria_dano": {
                "type": "string",
                "enum": CATEGORIAS_VALIDAS,
            },
            "nivel_riesgo": {
                "type": "string",
                "enum": NIVELES_RIESGO,
            },
            "confianza_analisis": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "costo_estimado": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": [
                    "moneda",
                    "minimo",
                    "maximo",
                    "mas_probable",
                    "desglose",
                    "supuestos",
                    "confianza_costo",
                ],
                "properties": {
                    "moneda": {"type": "string", "enum": ["BOB"]},
                    "minimo": {"type": "integer", "minimum": 0},
                    "maximo": {"type": "integer", "minimum": 0},
                    "mas_probable": {"type": "integer", "minimum": 0},
                    "desglose": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["concepto", "min", "max"],
                            "properties": {
                                "concepto": {"type": "string"},
                                "min": {"type": "integer", "minimum": 0},
                                "max": {"type": "integer", "minimum": 0},
                            },
                        },
                    },
                    "supuestos": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confianza_costo": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
            },
        },
    },
}


SYSTEM_PROMPT = f"""Eres un perito automotor experto en evaluación de daños
de vehículos para el mercado boliviano (Santa Cruz). Analizas UNA imagen
y devuelves un análisis estructurado en JSON.

Reglas estrictas:
1. Habla en español neutro, sin tecnicismos innecesarios.
2. Categoría: elige UNA de {CATEGORIAS_VALIDAS}. Si dudas, "general".
3. Nivel de riesgo: BAJO (estético), MEDIO (funcional menor),
   ALTO (compromete seguridad), CRITICO (vehículo inutilizable).
4. Estimación de costo: usa los siguientes anclajes reales del mercado:

{PRICE_REFERENCE_BOB}

5. ANTI-ALUCINACIÓN DE NÚMEROS — si la imagen no permite estimar con
   confianza_costo > 0.4 (borrosa, irrelevante, oscura, no muestra el
   daño), DEBES devolver costo_estimado.confianza_costo < 0.4 y un rango
   amplio (minimo=0, maximo=el techo de la categoría). Es PREFERIBLE
   abstenerse a inventar números.
6. mas_probable debe estar entre minimo y maximo.
7. El desglose lista componentes con sus rangos (mano de obra, repuestos,
   pintura, etc.). La suma de los `min` debe ser ≤ minimo total, y la
   suma de los `max` debe ser ≥ maximo total.
8. supuestos lista hipótesis clave que asumiste para estimar (ej. "asume
   daño superficial sin chasis afectado", "asume repuesto genérico no
   original").

NO incluyas texto fuera del JSON. NO inventes datos del vehículo que no
veas. Si la imagen no muestra un vehículo, devuelve categoria_dano="general",
nivel_riesgo="BAJO", confianza_analisis<0.2 y costo_estimado=null.
"""


# ── Dataclasses de retorno ─────────────────────────────────────────────────


@dataclass(slots=True)
class CostoBreakdownItem:
    concepto: str
    minimo: int
    maximo: int


@dataclass(slots=True)
class CostoEstimado:
    moneda: str
    minimo: int
    maximo: int
    mas_probable: int
    desglose: list[CostoBreakdownItem]
    supuestos: list[str]
    confianza_costo: float


@dataclass(slots=True)
class OpenAIVisionResult:
    """Resultado canónico de la visión OpenAI.

    `fallback=True` indica que la llamada falló y devolvemos valores
    degradados — el caller debe marcar requiere_revision_humana.
    """

    descripcion: str
    categoria_dano: str
    nivel_riesgo: str
    confianza_analisis: float
    costo_estimado: CostoEstimado | None
    latency_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    fallback: bool = False
    error: str | None = None
    raw_json: dict[str, Any] | None = field(default=None, repr=False)


# ── Helpers internos ───────────────────────────────────────────────────────


def _build_data_url(image_bytes: bytes, mime_type: str | None) -> str:
    mime = (mime_type or "image/jpeg").strip() or "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _degraded_result(
    *,
    latency_ms: int,
    error: str | None,
    model: str = "",
) -> OpenAIVisionResult:
    return OpenAIVisionResult(
        descripcion="",
        categoria_dano="general",
        nivel_riesgo="MEDIO",
        confianza_analisis=0.0,
        costo_estimado=None,
        latency_ms=latency_ms,
        model=model,
        fallback=True,
        error=error,
    )


def _parse_payload(payload: dict[str, Any]) -> OpenAIVisionResult | None:
    """Valida la forma del JSON. Devuelve None si algo está mal."""
    try:
        desc = str(payload["descripcion"])
        cat = str(payload["categoria_dano"])
        if cat not in CATEGORIAS_VALIDAS:
            return None
        riesgo = str(payload["nivel_riesgo"])
        if riesgo not in NIVELES_RIESGO:
            return None
        conf = float(payload["confianza_analisis"])
        if not 0.0 <= conf <= 1.0:
            return None

        costo_raw = payload.get("costo_estimado")
        costo: CostoEstimado | None = None
        if isinstance(costo_raw, dict):
            mini = int(costo_raw["minimo"])
            maxi = int(costo_raw["maximo"])
            prob = int(costo_raw["mas_probable"])
            if not (0 <= mini <= prob <= maxi):
                # Modelo se contradijo a sí mismo (min>max o probable fuera
                # de rango). NO aceptamos una respuesta basura — devolvemos
                # None para que el caller dispare el retry con temperature=0.
                return None
            else:
                conf_costo = float(costo_raw["confianza_costo"])
                desglose_items: list[CostoBreakdownItem] = []
                for item in costo_raw.get("desglose") or []:
                    if isinstance(item, dict):
                        desglose_items.append(
                            CostoBreakdownItem(
                                concepto=str(item.get("concepto", "")),
                                minimo=int(item.get("min", 0)),
                                maximo=int(item.get("max", 0)),
                            )
                        )
                costo = CostoEstimado(
                    moneda=str(costo_raw.get("moneda", "BOB")),
                    minimo=mini,
                    maximo=maxi,
                    mas_probable=prob,
                    desglose=desglose_items,
                    supuestos=[str(s) for s in (costo_raw.get("supuestos") or [])],
                    confianza_costo=max(0.0, min(1.0, conf_costo)),
                )

        return OpenAIVisionResult(
            descripcion=desc,
            categoria_dano=cat,
            nivel_riesgo=riesgo,
            confianza_analisis=conf,
            costo_estimado=costo,
            latency_ms=0,
            raw_json=payload,
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _call_openai(
    *,
    api_key: str,
    model: str,
    image_bytes: bytes,
    mime_type: str | None,
    context: str,
    temperature: float,
    timeout_s: float,
) -> tuple[dict[str, Any] | None, int, int, str | None]:
    """Hace UNA llamada a OpenAI. Devuelve (payload_or_None, tokens_in,
    tokens_out, error_or_None)."""
    body = {
        "model": model,
        "temperature": temperature,
        "response_format": {
            "type": "json_schema",
            "json_schema": VISION_RESPONSE_SCHEMA,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Contexto del reporte del cliente: {context or '(sin contexto)'}\n\n"
                            "Analiza la imagen adjunta y devuelve el JSON."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _build_data_url(image_bytes, mime_type)},
                    },
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(OPENAI_CHAT_URL, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        # 429/5xx: reintentable; 4xx (excepto 429) no — caller decide.
        return None, 0, 0, f"HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return None, 0, 0, f"network: {type(exc).__name__}"
    except Exception as exc:
        return None, 0, 0, f"unknown: {type(exc).__name__}"

    try:
        choices = data.get("choices") or []
        if not choices:
            return None, 0, 0, "no_choices"
        content = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0) or 0)
        tokens_out = int(usage.get("completion_tokens", 0) or 0)
        payload = json.loads(content)
        if not isinstance(payload, dict):
            return None, tokens_in, tokens_out, "json_not_object"
        return payload, tokens_in, tokens_out, None
    except (json.JSONDecodeError, TypeError, KeyError):
        return None, 0, 0, "json_parse_error"


# ── API pública ─────────────────────────────────────────────────────────────


async def analyze_vehicle_image(
    *,
    image_bytes: bytes,
    mime_type: str | None,
    context: str = "",
) -> OpenAIVisionResult:
    """Analiza la imagen de un vehículo dañado y estima costo de reparación.

    NUNCA lanza excepción. Si OpenAI falla, devuelve un resultado con
    fallback=True y costo_estimado=None.
    """
    settings = get_settings()
    api_key = (settings.openai_api_key or "").strip()
    model = settings.openai_vision_model or "gpt-4o-mini"
    timeout = float(settings.openai_vision_timeout_s)

    started = time.perf_counter()

    if len(api_key) < 10:
        logger.warning("OpenAI vision: API key no configurada — fallback.")
        return _degraded_result(
            latency_ms=int((time.perf_counter() - started) * 1000),
            error="openai_api_key_missing",
            model=model,
        )
    if not image_bytes:
        return _degraded_result(
            latency_ms=int((time.perf_counter() - started) * 1000),
            error="empty_image",
            model=model,
        )

    # Intento 1: temperature=0.2.
    payload, tokens_in, tokens_out, err = await _call_openai(
        api_key=api_key,
        model=model,
        image_bytes=image_bytes,
        mime_type=mime_type,
        context=context,
        temperature=0.2,
        timeout_s=timeout,
    )
    result: OpenAIVisionResult | None = None
    if payload is not None:
        result = _parse_payload(payload)

    if result is None:
        # Intento 2: temperature=0 (más estricto). Cuenta como retry único.
        logger.info("OpenAI vision: retry con temperature=0 (err=%s)", err)
        payload2, t_in2, t_out2, err2 = await _call_openai(
            api_key=api_key,
            model=model,
            image_bytes=image_bytes,
            mime_type=mime_type,
            context=context,
            temperature=0.0,
            timeout_s=timeout,
        )
        tokens_in += t_in2
        tokens_out += t_out2
        if payload2 is not None:
            result = _parse_payload(payload2)
            err = err2 if result is None else None

    latency_ms = int((time.perf_counter() - started) * 1000)

    if result is None:
        # No registramos contenido de imagen ni claves — solo el error.
        logger.warning(
            "OpenAI vision falló tras retry — error=%s, latency_ms=%d", err, latency_ms,
        )
        return _degraded_result(latency_ms=latency_ms, error=err, model=model)

    result.latency_ms = latency_ms
    result.tokens_in = tokens_in
    result.tokens_out = tokens_out
    result.model = model
    # Estimación de costo en USD (gpt-4o-mini, precios oct-2024):
    # input ≈ $0.15/1M, output ≈ $0.60/1M.
    cost_usd = (tokens_in * 0.15 + tokens_out * 0.60) / 1_000_000.0
    logger.info(
        "OpenAI vision OK — modelo=%s, tokens_in=%d, tokens_out=%d, "
        "cost_usd=%.6f, latency_ms=%d, conf_analisis=%.2f, conf_costo=%.2f",
        model, tokens_in, tokens_out, cost_usd, latency_ms,
        result.confianza_analisis,
        result.costo_estimado.confianza_costo if result.costo_estimado else 0.0,
    )
    return result
