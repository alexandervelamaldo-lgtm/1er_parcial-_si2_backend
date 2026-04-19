from dataclasses import dataclass


@dataclass(slots=True)
class TriageResult:
    confidence: float
    requires_manual_review: bool
    summary: str
    reason: str
    detected_tags: list[str]
    provider: str


@dataclass(slots=True)
class CostEstimateResult:
    amount: float
    min_amount: float
    max_amount: float
    confidence: float
    note: str


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


def estimate_repair_cost(
    *,
    tipo_incidente: str,
    descripcion: str,
    es_carretera: bool,
    condicion_vehiculo: str,
    nivel_riesgo: int,
    detected_tags: list[str] | None = None,
    clasificacion_confianza: float | None = None,
    requiere_revision_manual: bool = False,
    prioridad: str | None = None,
    transcripcion_audio: str | None = None,
    resumen_ia: str | None = None,
) -> CostEstimateResult:
    normalized_type = tipo_incidente.strip().lower()
    normalized_description = descripcion.strip().lower()
    normalized_condition = condicion_vehiculo.strip().lower()
    normalized_audio = (transcripcion_audio or "").strip().lower()
    normalized_summary = (resumen_ia or "").strip().lower()
    full_text = " ".join(
        part
        for part in [normalized_type, normalized_description, normalized_condition, normalized_audio, normalized_summary]
        if part
    )
    tags = sorted(set(tag for tag in (detected_tags or []) if tag))

    base_amount = 280.0
    type_rules = {
        "accidente": 980.0,
        "choque": 980.0,
        "colisión": 980.0,
        "colision": 980.0,
        "falla mecánica": 620.0,
        "falla mecanica": 620.0,
        "bloqueo de tráfico": 760.0,
        "bloqueo de trafico": 760.0,
        "grúa": 420.0,
        "grua": 420.0,
        "llanta ponchada": 190.0,
        "pinchazo": 190.0,
        "batería": 170.0,
        "bateria": 170.0,
        "sin combustible": 140.0,
        "combustible": 140.0,
    }
    for label, amount in type_rules.items():
        if label in normalized_type:
            base_amount = amount
            break

    total = base_amount
    tag_adjustments = {
        "bateria": 120.0,
        "llanta": 90.0,
        "motor": 260.0,
        "choque": 420.0,
        "check_engine": 160.0,
        "combustible": 80.0,
    }
    for tag in tags:
        total += tag_adjustments.get(tag, 0.0)

    keyword_adjustments = {
        "remolque": 280.0,
        "grua": 240.0,
        "grúa": 240.0,
        "humo": 180.0,
        "aceite": 95.0,
        "freno": 150.0,
        "tablero": 80.0,
        "airbag": 260.0,
        "radiador": 170.0,
        "dirección": 130.0,
        "direccion": 130.0,
        "suspensión": 140.0,
        "suspension": 140.0,
    }
    for keyword, adjustment in keyword_adjustments.items():
        if keyword in full_text:
            total += adjustment

    if es_carretera:
        total += 90.0

    total += max(0, min(nivel_riesgo, 5) - 1) * 55.0

    if "inmovilizado" in normalized_condition or "no arranca" in normalized_condition:
        total += 220.0
    elif "limitaciones" in normalized_condition:
        total += 80.0

    if prioridad == "ALTA":
        total += 60.0
    elif prioridad == "CRITICA":
        total += 130.0

    if "pérdida total" in full_text or "perdida total" in full_text:
        total += 500.0
    if "volc" in full_text:
        total += 320.0

    amount = round(max(total, 90.0) / 10) * 10
    variability = 0.16 + (0.03 * max(0, min(nivel_riesgo, 5) - 1))
    if es_carretera:
        variability += 0.03
    if requiere_revision_manual:
        variability += 0.07
    if not tags:
        variability += 0.02
    variability = min(0.4, variability)

    min_amount = round(max(60.0, amount * (1 - variability)) / 10) * 10
    max_amount = round(max(amount + 40.0, amount * (1 + variability)) / 10) * 10
    if max_amount <= min_amount:
        max_amount = min_amount + 40.0

    confidence_base = clasificacion_confianza if clasificacion_confianza is not None else 0.62
    confidence = confidence_base + min(0.12, len(tags) * 0.03)
    if transcripcion_audio:
        confidence += 0.04
    if resumen_ia:
        confidence += 0.03
    if requiere_revision_manual:
        confidence -= 0.12
    confidence = max(0.25, min(round(confidence, 2), 0.95))

    note_parts = [
        "Monto estimado en Bs con reglas IA sobre tipo de incidente, riesgo y señales detectadas",
    ]
    if tags:
        note_parts.append(f"Señales consideradas: {', '.join(tags)}")
    note_parts.append("Incluye un rango aproximado en bolivianos y puede ajustarse tras la atención técnica")

    return CostEstimateResult(
        amount=float(amount),
        min_amount=float(min_amount),
        max_amount=float(max_amount),
        confidence=confidence,
        note=". ".join(note_parts),
    )
