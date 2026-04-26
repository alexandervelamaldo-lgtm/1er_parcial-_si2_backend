from dataclasses import dataclass
import base64
from pathlib import Path

import httpx

from app.config import get_settings


KEYWORD_MAP = {
    "bateria": ["bateria", "battery", "arranca", "corriente", "alternador"],
    "llanta": ["llanta", "ponchada", "pinchada", "neumatico", "rueda"],
    "motor": ["motor", "temperatura", "humo", "aceite", "vibracion"],
    "choque": ["choque", "colision", "accidente", "impacto", "golpe"],
    "check_engine": ["check engine", "motor", "tablero", "testigo"],
    "combustible": ["combustible", "gasolina", "diesel", "tanque"],
}


@dataclass(slots=True)
class AudioTranscriptionResult:
    transcript: str
    confidence: float
    provider: str


@dataclass(slots=True)
class ImageAnalysisResult:
    labels: list[str]
    summary: str
    confidence: float
    provider: str
    components: list[str]
    damage_zones: list[str]
    severity: str
    visual_factor: float


@dataclass(slots=True)
class ExternalAIResult:
    transcript: str | None = None
    labels: list[str] | None = None
    summary: str | None = None
    confidence: float | None = None
    provider: str = "external-http"
    components: list[str] | None = None
    damage_zones: list[str] | None = None
    severity: str | None = None
    visual_factor: float | None = None


def _extract_labels(*values: str) -> list[str]:
    normalized = " ".join(value.lower() for value in values if value)
    labels = [label for label, aliases in KEYWORD_MAP.items() if any(alias in normalized for alias in aliases)]
    return sorted(set(labels))


def _extract_components_and_damage(*values: str) -> tuple[list[str], list[str], str, float]:
    normalized = " ".join(value.lower() for value in values if value)
    component_rules = {
        "parachoques": ["parachoque", "paragolpe", "bumper"],
        "faro": ["faro", "fanal", "luces"],
        "capo": ["capo", "capó"],
        "radiador": ["radiador"],
        "llanta": ["llanta", "rueda", "neumatico", "neumático"],
        "motor": ["motor", "humo", "aceite"],
        "puerta": ["puerta"],
        "lateral": ["lateral", "costado"],
    }
    components = sorted(
        set(component for component, aliases in component_rules.items() if any(alias in normalized for alias in aliases))
    )
    zone_rules = {
        "frontal": ["frontal", "frente", "choque frontal"],
        "lateral": ["lateral", "costado"],
        "trasera": ["trasera", "atras", "atrás", "posterior"],
    }
    damage_zones = sorted(set(zone for zone, aliases in zone_rules.items() if any(alias in normalized for alias in aliases)))

    severity = "LEVE"
    visual_factor = 1.03
    if any(token in normalized for token in ["abolladura", "rayon", "rayón", "leve"]):
        severity = "LEVE"
        visual_factor = 1.05
    if any(token in normalized for token in ["moderado", "parachoque", "faro roto", "lateral"]):
        severity = "MODERADO"
        visual_factor = 1.12
    if any(token in normalized for token in ["severo", "airbag", "radiador", "estructural", "fuerte", "volcado"]):
        severity = "SEVERO"
        visual_factor = 1.28
    if any(token in normalized for token in ["total", "irreparable", "pérdida total", "perdida total"]):
        severity = "CRITICO"
        visual_factor = 1.4
    return components, damage_zones, severity, visual_factor


async def _call_external_provider(kind: str, payload: dict[str, str | int | float]) -> ExternalAIResult | None:
    settings = get_settings()
    if settings.ai_provider != "http" or not settings.ai_http_endpoint:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.ai_api_key:
        headers["Authorization"] = f"Bearer {settings.ai_api_key}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                settings.ai_http_endpoint.rstrip("/") + f"/{kind}",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    return ExternalAIResult(
        transcript=data.get("transcript"),
        labels=list(data.get("labels", [])),
        summary=data.get("summary"),
        confidence=float(data["confidence"]) if data.get("confidence") is not None else None,
        components=list(data.get("components", [])) if isinstance(data.get("components"), list) else None,
        damage_zones=list(data.get("damage_zones", [])) if isinstance(data.get("damage_zones"), list) else None,
        severity=data.get("severity"),
        visual_factor=float(data["visual_factor"]) if data.get("visual_factor") is not None else None,
    )


async def transcribe_audio_file(
    file_name: str,
    mime_type: str | None,
    size_bytes: int,
    file_bytes: bytes | None = None,
) -> AudioTranscriptionResult:
    payload: dict[str, str | int | float] = {"file_name": file_name, "mime_type": mime_type or "", "size_bytes": size_bytes}
    if file_bytes:
        payload["file_base64"] = base64.b64encode(file_bytes).decode("ascii")
    external = await _call_external_provider(
        "transcribe",
        payload,
    )
    if external and external.transcript:
        return AudioTranscriptionResult(
            transcript=external.transcript,
            confidence=external.confidence or 0.82,
            provider=external.provider,
        )
    labels = _extract_labels(Path(file_name).stem)
    transcript = (
        f"Reporte de audio recibido. Posibles señales detectadas: {', '.join(labels)}."
        if labels
        else "Reporte de audio recibido. Se solicita confirmar batería, llanta, motor o choque."
    )
    return AudioTranscriptionResult(transcript=transcript, confidence=0.58 if labels else 0.42, provider="mock")


async def analyze_image_file(
    file_name: str,
    mime_type: str | None,
    context: str,
    file_bytes: bytes | None = None,
) -> ImageAnalysisResult:
    payload: dict[str, str | int | float] = {"file_name": file_name, "mime_type": mime_type or "", "context": context}
    if file_bytes:
        payload["file_base64"] = base64.b64encode(file_bytes).decode("ascii")
    external = await _call_external_provider(
        "vision",
        payload,
    )
    if external and external.labels is not None:
        labels = external.labels
        summary = external.summary or "Análisis de imagen completado"
        confidence = external.confidence or 0.8
        components = external.components or []
        damage_zones = external.damage_zones or []
        severity = external.severity or "MODERADO"
        visual_factor = external.visual_factor or (1.22 if severity in {"SEVERO", "CRITICO"} else 1.12)
        return ImageAnalysisResult(
            labels=labels,
            summary=summary,
            confidence=confidence,
            provider=external.provider,
            components=components,
            damage_zones=damage_zones,
            severity=severity,
            visual_factor=visual_factor,
        )
    labels = _extract_labels(Path(file_name).stem, context)
    components, damage_zones, severity, visual_factor = _extract_components_and_damage(Path(file_name).stem, context)
    summary = (
        f"La evidencia visual sugiere: {', '.join(labels)}."
        if labels
        else "La evidencia visual no aporta una clase concluyente."
    )
    if components:
        summary = f"{summary} Componentes afectados: {', '.join(components)}. Severidad visual: {severity}."
    confidence = 0.76 if labels or components else 0.48
    return ImageAnalysisResult(
        labels=labels,
        summary=summary,
        confidence=confidence,
        provider="mock",
        components=components,
        damage_zones=damage_zones,
        severity=severity,
        visual_factor=visual_factor,
    )
