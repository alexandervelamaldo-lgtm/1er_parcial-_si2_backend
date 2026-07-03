from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unicodedata

from app.models.enums import CategoriaDano, normalizar_categoria_dano
from app.models.solicitudes import Solicitud
from app.services.mapa.travel_time_policy import estimate_eta_minutes
from app.models.tecnicos import Tecnico
from app.utils.geo import calcular_distancia_km


@dataclass(slots=True)
class DemandaMatch:
    tecnico_id: int
    distancia_km: float
    eta_min: int
    match_especialidad: bool
    score: float
    detalle: str
    cobertura_valida: bool


# Keywords de especialidad por CategoriaDano estandarizada
SPECIALTY_KEYWORDS: dict[CategoriaDano, tuple[str, ...]] = {
    CategoriaDano.DANO_ELECTRICO: ("electr", "bateria", "alternador", "inyeccion", "scan"),
    CategoriaDano.PINCHAZO: ("llanta", "neumatic", "goma", "alineacion", "balanceo"),
    CategoriaDano.FALLA_MECANICA: ("motor", "mecan", "inyeccion", "freno", "transmision"),
    CategoriaDano.SUSPENSION: ("suspension", "amortigu", "tren delantero", "direccion"),
    CategoriaDano.CHOQUE_CARROCERIA: ("chaper", "pintura", "carroceria", "latoneria"),
    CategoriaDano.CHAPERIA_PINTURA: ("chaper", "pintura", "carroceria", "latoneria"),
    CategoriaDano.GENERAL: ("general", "mecan", "integral", "multiservicio"),
}


def tecnico_operativamente_disponible(tecnico: Tecnico) -> bool:
    return bool(tecnico.disponibilidad and getattr(tecnico, "en_turno", True))


def _normalize_text(value: str | None) -> str:
    base = unicodedata.normalize("NFKD", (value or "").strip().lower())
    return "".join(char for char in base if not unicodedata.combining(char))


def especialidad_compatible(categoria_dano: str | None, especialidad: str | None) -> bool:
    cat = normalizar_categoria_dano(categoria_dano)
    especialidad_normalizada = _normalize_text(especialidad)
    if not especialidad_normalizada:
        return cat == CategoriaDano.GENERAL
    keywords = SPECIALTY_KEYWORDS.get(cat) or SPECIALTY_KEYWORDS[CategoriaDano.GENERAL]
    if cat == CategoriaDano.GENERAL:
        return True
    return any(kw in especialidad_normalizada for kw in keywords)


def ubicacion_tecnico_reciente(tecnico: Tecnico, *, max_age_minutes: int = 20) -> bool:
    updated_at = tecnico.ubicacion_actualizada_en
    if updated_at is None:
        return False
    reference = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - reference <= timedelta(minutes=max_age_minutes)


def calcular_match_tecnico(
    solicitud: Solicitud,
    tecnico: Tecnico,
    *,
    max_radio_km: float,
) -> DemandaMatch | None:
    if tecnico.latitud_actual is None or tecnico.longitud_actual is None:
        return None
    if not tecnico_operativamente_disponible(tecnico):
        return None

    distancia = calcular_distancia_km(
        solicitud.latitud_incidente,
        solicitud.longitud_incidente,
        tecnico.latitud_actual,
        tecnico.longitud_actual,
    )
    cobertura = float(getattr(tecnico, "radio_cobertura_km", max_radio_km) or max_radio_km)
    radio_efectivo = min(max_radio_km, cobertura)
    if distancia > radio_efectivo:
        return None

    match_especialidad = especialidad_compatible(solicitud.categoria_dano, tecnico.especialidad)
    score = round(
        (45 if match_especialidad else 10)
        + max(0, 35 - distancia)
        + min(cobertura, 30) * 0.6
        + (8 if ubicacion_tecnico_reciente(tecnico) else 0),
        2,
    )
    detalle = (
        "Cobertura y especialidad compatibles con la solicitud."
        if match_especialidad
        else "Se prioriza cercanía y disponibilidad aunque la especialidad no es exacta."
    )
    return DemandaMatch(
        tecnico_id=tecnico.id,
        distancia_km=round(distancia, 2),
        eta_min=estimate_eta_minutes(distancia),
        match_especialidad=match_especialidad,
        score=score,
        detalle=detalle,
        cobertura_valida=distancia <= cobertura,
    )
