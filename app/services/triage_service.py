from dataclasses import dataclass


@dataclass(slots=True)
class TriageResult:
    confidence: float
    requires_manual_review: bool
    summary: str
    reason: str
    detected_tags: list[str]
    provider: str


def analyze_incident(
    *,
    tipo_incidente: str,
    descripcion: str,
    es_carretera: bool,
    condicion_vehiculo: str,
    nivel_riesgo: int,
) -> TriageResult:
    score = 0.45
    normalized_description = descripcion.strip()
    normalized_condition = condicion_vehiculo.lower()
    normalized_full_text = f"{tipo_incidente} {normalized_description} {normalized_condition}".lower()
    detected_tags = [
        label
        for label, aliases in {
            "bateria": ["bateria", "battery", "arranca", "corriente", "alternador"],
            "llanta": ["llanta", "ponchada", "pinchada", "neumatico", "rueda"],
            "motor": ["motor", "temperatura", "humo", "aceite", "vibracion"],
            "choque": ["choque", "colision", "accidente", "impacto", "golpe"],
            "check_engine": ["check engine", "testigo", "tablero"],
            "combustible": ["combustible", "gasolina", "diesel", "tanque"],
        }.items()
        if any(alias in normalized_full_text for alias in aliases)
    ]

    if len(normalized_description) >= 20:
        score += 0.15
    if len(normalized_description) >= 60:
        score += 0.1
    if es_carretera:
        score += 0.05
    if nivel_riesgo >= 4:
        score += 0.1
    if tipo_incidente.lower() in {"accidente", "falla mecánica", "bloqueo de tráfico"}:
        score += 0.1
    if "inmovilizado" in normalized_condition or "no arranca" in normalized_condition:
        score += 0.1
    if detected_tags:
        score += min(0.12, len(detected_tags) * 0.03)

    confidence = max(0.2, min(round(score, 2), 0.98))
    requires_manual_review = confidence < 0.65 or len(normalized_description) < 15
    reason_parts = [
        f"Tipo detectado: {tipo_incidente}",
        f"Nivel de riesgo reportado: {nivel_riesgo}/5",
        "Incidente en carretera" if es_carretera else "Incidente en zona urbana",
        f"Condición declarada: {condicion_vehiculo}",
    ]
    summary = (
        "La IA sugiere atención priorizada"
        if not requires_manual_review
        else "La IA requiere validación manual por baja confianza o información insuficiente"
    )
    return TriageResult(
        confidence=confidence,
        requires_manual_review=requires_manual_review,
        summary=summary,
        reason=" | ".join(reason_parts),
        detected_tags=sorted(set(detected_tags)),
        provider="rule-engine",
    )
