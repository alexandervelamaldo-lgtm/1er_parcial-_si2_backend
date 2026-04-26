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


BOLIVIA_BASE_COST_BS = {
    "accidente": 2200.0,
    "choque": 2200.0,
    "colisión": 2200.0,
    "colision": 2200.0,
    "falla mecánica": 1200.0,
    "falla mecanica": 1200.0,
    "bloqueo de tráfico": 900.0,
    "bloqueo de trafico": 900.0,
    "grúa": 650.0,
    "grua": 650.0,
    "llanta ponchada": 320.0,
    "pinchazo": 320.0,
    "batería": 380.0,
    "bateria": 380.0,
    "sin combustible": 260.0,
    "combustible": 260.0,
}

ANTIGUEDAD_FACTOR = {
    "moderno": 0.93,  # >=2020
    "medio": 1.0,  # 2013-2019
    "antiguo": 1.12,  # 2006-2012
    "muy_antiguo": 1.2,  # <=2005
}

VEHICULO_COMPLEJIDAD_FACTOR = {
    "economico": 0.92,
    "medio": 1.0,
    "premium": 1.2,
}

PRIORIDAD_FACTOR = {
    "BAJA": 0.95,
    "MEDIA": 1.0,
    "ALTA": 1.08,
    "CRITICA": 1.18,
}

REGION_BOLIVIA_FACTOR = {
    "la paz": 1.06,
    "el alto": 1.05,
    "santa cruz": 1.08,
    "cochabamba": 1.03,
    "oruro": 0.98,
    "potosi": 0.97,
    "tarija": 1.02,
    "sucre": 1.0,
    "chuquisaca": 1.0,
    "beni": 1.04,
    "pando": 1.06,
}

EVIDENCE_FACTOR_BY_TAG = {
    "bateria": 1.03,
    "llanta": 1.05,
    "motor": 1.18,
    "choque": 1.22,
    "check_engine": 1.1,
    "combustible": 1.02,
}

PREMIUM_BRANDS = {"bmw", "mercedes", "audi", "lexus", "volvo", "mini", "porsche", "land rover", "jeep"}
ECONOMIC_BRANDS = {"suzuki", "toyota", "nissan", "hyundai", "kia", "renault", "chevrolet", "fiat"}


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
    vehiculo_marca: str | None = None,
    vehiculo_modelo: str | None = None,
    vehiculo_anio: int | None = None,
    region_hint: str | None = None,
) -> CostEstimateResult:
    normalized_type = tipo_incidente.strip().lower()
    normalized_description = descripcion.strip().lower()
    normalized_condition = condicion_vehiculo.strip().lower()
    normalized_audio = (transcripcion_audio or "").strip().lower()
    normalized_summary = (resumen_ia or "").strip().lower()
    normalized_brand = (vehiculo_marca or "").strip().lower()
    normalized_model = (vehiculo_modelo or "").strip().lower()
    normalized_region = (region_hint or "").strip().lower()
    full_text = " ".join(
        part
        for part in [
            normalized_type,
            normalized_description,
            normalized_condition,
            normalized_audio,
            normalized_summary,
            normalized_brand,
            normalized_model,
            normalized_region,
        ]
        if part
    )
    tags = sorted(set(tag for tag in (detected_tags or []) if tag))

    base_amount = 650.0
    selected_type = "general"
    for label, amount in BOLIVIA_BASE_COST_BS.items():
        if label in normalized_type:
            base_amount = amount
            selected_type = label
            break

    if vehiculo_anio is None:
        antiguedad_factor = 1.06
        antiguedad_band = "fallback"
    elif vehiculo_anio >= 2020:
        antiguedad_factor = ANTIGUEDAD_FACTOR["moderno"]
        antiguedad_band = ">=2020"
    elif vehiculo_anio >= 2013:
        antiguedad_factor = ANTIGUEDAD_FACTOR["medio"]
        antiguedad_band = "2013-2019"
    elif vehiculo_anio >= 2006:
        antiguedad_factor = ANTIGUEDAD_FACTOR["antiguo"]
        antiguedad_band = "2006-2012"
    else:
        antiguedad_factor = ANTIGUEDAD_FACTOR["muy_antiguo"]
        antiguedad_band = "<=2005"

    if any(brand in normalized_brand for brand in PREMIUM_BRANDS):
        complejidad_factor = VEHICULO_COMPLEJIDAD_FACTOR["premium"]
        complejidad_bucket = "premium"
    elif any(brand in normalized_brand for brand in ECONOMIC_BRANDS):
        complejidad_factor = VEHICULO_COMPLEJIDAD_FACTOR["economico"]
        complejidad_bucket = "economico"
    elif normalized_brand:
        complejidad_factor = VEHICULO_COMPLEJIDAD_FACTOR["medio"]
        complejidad_bucket = "medio"
    else:
        complejidad_factor = 1.03
        complejidad_bucket = "fallback"

    risk_factor = 0.9 + (max(0, min(nivel_riesgo, 5)) * 0.08)
    prioridad_factor = PRIORIDAD_FACTOR.get((prioridad or "MEDIA").upper(), 1.0)
    severidad_factor = round(risk_factor * prioridad_factor, 3)

    region_factor = 1.0
    selected_region = "bolivia"
    for region_key, value in REGION_BOLIVIA_FACTOR.items():
        if region_key in normalized_region:
            region_factor = value
            selected_region = region_key
            break

    evidence_factor = 1.0
    for tag in tags:
        evidence_factor *= EVIDENCE_FACTOR_BY_TAG.get(tag, 1.0)
    if "humo" in full_text or "aceite" in full_text or "airbag" in full_text:
        evidence_factor *= 1.07
    if "remolque" in full_text or "grua" in full_text or "grúa" in full_text:
        evidence_factor *= 1.08
    if es_carretera:
        evidence_factor *= 1.06
    if "inmovilizado" in normalized_condition or "no arranca" in normalized_condition:
        evidence_factor *= 1.09
    elif "limitaciones" in normalized_condition:
        evidence_factor *= 1.04
    evidence_factor = min(1.65, max(0.88, evidence_factor))

    total = base_amount * antiguedad_factor * complejidad_factor * severidad_factor * region_factor * evidence_factor
    if "volc" in full_text:
        total *= 1.15
    if "pérdida total" in full_text or "perdida total" in full_text:
        total *= 1.22

    amount = round(max(total, 120.0) / 10) * 10

    data_completeness = 0.55
    if vehiculo_marca and vehiculo_modelo:
        data_completeness += 0.12
    if vehiculo_anio:
        data_completeness += 0.1
    if region_hint:
        data_completeness += 0.08
    if descripcion and len(descripcion.strip()) >= 20:
        data_completeness += 0.08

    consistency = 0.62
    if tags:
        consistency += 0.12
    if any(token in normalized_description for token in ["choque", "llanta", "motor", "bateria", "batería"]):
        consistency += 0.08
    if tags and not any(tag in full_text for tag in tags):
        consistency -= 0.1

    evidence_quality = 0.58
    if transcripcion_audio:
        evidence_quality += 0.12
    if resumen_ia:
        evidence_quality += 0.08
    if len(tags) >= 2:
        evidence_quality += 0.08
    elif not tags:
        evidence_quality -= 0.06

    confidence_base = clasificacion_confianza if clasificacion_confianza is not None else 0.6
    confidence = (confidence_base * 0.3) + (data_completeness * 0.3) + (consistency * 0.2) + (evidence_quality * 0.2)
    if confidence_base >= 0.8:
        confidence += 0.04
    if nivel_riesgo >= 4 and len(tags) >= 2:
        confidence += 0.05
    if (prioridad or "").upper() == "CRITICA":
        confidence += 0.03
    if requiere_revision_manual:
        confidence -= 0.1
    confidence = max(0.25, min(round(confidence, 2), 0.95))

    margin = min(0.4, max(0.12, 0.36 - (confidence * 0.22)))
    min_amount = round(max(90.0, amount * (1 - margin)) / 10) * 10
    max_amount = round(max(amount + 60.0, amount * (1 + margin)) / 10) * 10
    if max_amount <= min_amount:
        max_amount = min_amount + 60.0

    note_parts = [
        f"Bolivia: base {selected_type}={round(base_amount, 2)} Bs",
        f"f_antiguedad={antiguedad_factor} ({antiguedad_band})",
        f"f_complejidad={complejidad_factor} ({complejidad_bucket})",
        f"f_severidad={round(severidad_factor, 3)} (riesgo={nivel_riesgo}, prioridad={(prioridad or 'MEDIA').upper()})",
        f"f_region={region_factor} ({selected_region})",
        f"f_evidencia={round(evidence_factor, 3)}",
        f"margen={round(margin, 3)} por confianza={confidence}",
    ]
    if tags:
        note_parts.append(f"Señales consideradas: {', '.join(tags)}")
    if requiere_revision_manual:
        note_parts.append("Penalización por revisión manual pendiente")
    note_parts.append("Estimación referencial para mercado boliviano; sujeta a inspección técnica final")

    return CostEstimateResult(
        amount=float(amount),
        min_amount=float(min_amount),
        max_amount=float(max_amount),
        confidence=confidence,
        note=". ".join(note_parts),
    )
