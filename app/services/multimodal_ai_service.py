from dataclasses import dataclass
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


@dataclass(slots=True)
class ExternalAIResult:
    transcript: str | None = None
    labels: list[str] | None = None
    summary: str | None = None
    confidence: float | None = None
    provider: str = "external-http"


def _extract_labels(*values: str) -> list[str]:
    normalized = " ".join(value.lower() for value in values if value)
    labels = [label for label, aliases in KEYWORD_MAP.items() if any(alias in normalized for alias in aliases)]
    return sorted(set(labels))


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
    )


async def transcribe_audio_file(file_name: str, mime_type: str | None, size_bytes: int) -> AudioTranscriptionResult:
    external = await _call_external_provider(
        "transcribe",
        {"file_name": file_name, "mime_type": mime_type or "", "size_bytes": size_bytes},
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


async def analyze_image_file(file_name: str, mime_type: str | None, context: str) -> ImageAnalysisResult:
    external = await _call_external_provider(
        "vision",
        {"file_name": file_name, "mime_type": mime_type or "", "context": context},
    )
    if external and external.labels is not None:
        labels = external.labels
        summary = external.summary or "Análisis de imagen completado"
        confidence = external.confidence or 0.8
        return ImageAnalysisResult(labels=labels, summary=summary, confidence=confidence, provider=external.provider)
    labels = _extract_labels(Path(file_name).stem, context)
    summary = (
        f"La evidencia visual sugiere: {', '.join(labels)}."
        if labels
        else "La evidencia visual no aporta una clase concluyente."
    )
    confidence = 0.73 if labels else 0.48
    return ImageAnalysisResult(labels=labels, summary=summary, confidence=confidence, provider="mock")
