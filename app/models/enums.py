import unicodedata
from enum import Enum


class NombreRol(str, Enum):
    CLIENTE = "CLIENTE"
    TECNICO = "TECNICO"
    TALLER = "TALLER"
    OPERADOR = "OPERADOR"
    ADMINISTRADOR = "ADMINISTRADOR"


class PrioridadSolicitud(str, Enum):
    BAJA = "BAJA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"
    CRITICA = "CRITICA"


class EstadoSolicitudEnum(str, Enum):
    REGISTRADA = "REGISTRADA"
    # Cliente eligió un taller en la app móvil y el taller aún no responde.
    # Esto es nuevo del flujo cliente↔taller-directo (sin operador como
    # intermediario). El taller recibe push y debe aceptar o rechazar.
    PROPUESTA_TALLER = "PROPUESTA_TALLER"
    # El taller rechazó la propuesta — la solicitud vuelve a manos del
    # cliente para que elija a otro. Si esto pasa 3 veces seguidas, el
    # backend escala a OPERADOR vía notificación.
    RECHAZADA_TALLER = "RECHAZADA_TALLER"
    ASIGNADA = "ASIGNADA"
    EN_CAMINO = "EN_CAMINO"
    EN_ATENCION = "EN_ATENCION"
    COMPLETADA = "COMPLETADA"
    CANCELADA = "CANCELADA"


class CategoriaDano(str, Enum):
    """Tipos base de daño vehicular — extensible vía alias en normalizar_categoria_dano."""
    PINCHAZO = "pinchazo"
    CHOQUE_CARROCERIA = "choque_carroceria"
    DANO_ELECTRICO = "dano_electrico"
    FALLA_MECANICA = "falla_mecanica"
    SUSPENSION = "suspension"
    CHAPERIA_PINTURA = "chaperia_pintura"
    GENERAL = "general"


# Mapeo de alias de texto libre → CategoriaDano
_CATEGORIA_DANO_ALIASES: dict[str, CategoriaDano] = {
    # pinchazo
    "pinchazo": CategoriaDano.PINCHAZO,
    "llanta": CategoriaDano.PINCHAZO,
    "llantas": CategoriaDano.PINCHAZO,
    "llanta pinchada": CategoriaDano.PINCHAZO,
    "llanta ponchada": CategoriaDano.PINCHAZO,
    "neumatico": CategoriaDano.PINCHAZO,
    "neumático": CategoriaDano.PINCHAZO,
    "ponchada": CategoriaDano.PINCHAZO,
    "pinchada": CategoriaDano.PINCHAZO,
    "rueda": CategoriaDano.PINCHAZO,
    # choque_carroceria
    "choque": CategoriaDano.CHOQUE_CARROCERIA,
    "choque_carroceria": CategoriaDano.CHOQUE_CARROCERIA,
    "colision": CategoriaDano.CHOQUE_CARROCERIA,
    "colisión": CategoriaDano.CHOQUE_CARROCERIA,
    "accidente": CategoriaDano.CHOQUE_CARROCERIA,
    "impacto": CategoriaDano.CHOQUE_CARROCERIA,
    "chaperia": CategoriaDano.CHOQUE_CARROCERIA,
    "chapería": CategoriaDano.CHOQUE_CARROCERIA,
    "carroceria": CategoriaDano.CHOQUE_CARROCERIA,
    "carrocería": CategoriaDano.CHOQUE_CARROCERIA,
    "pintura": CategoriaDano.CHOQUE_CARROCERIA,
    "chapisteria": CategoriaDano.CHOQUE_CARROCERIA,
    "chaperia_pintura": CategoriaDano.CHOQUE_CARROCERIA,
    # dano_electrico
    "dano_electrico": CategoriaDano.DANO_ELECTRICO,
    "daño_electrico": CategoriaDano.DANO_ELECTRICO,
    "electricidad": CategoriaDano.DANO_ELECTRICO,
    "electrico": CategoriaDano.DANO_ELECTRICO,
    "eléctrico": CategoriaDano.DANO_ELECTRICO,
    "bateria": CategoriaDano.DANO_ELECTRICO,
    "batería": CategoriaDano.DANO_ELECTRICO,
    "bateria descargada": CategoriaDano.DANO_ELECTRICO,
    "batería descargada": CategoriaDano.DANO_ELECTRICO,
    "bateria_descargada": CategoriaDano.DANO_ELECTRICO,
    "alternador": CategoriaDano.DANO_ELECTRICO,
    "cortocircuito": CategoriaDano.DANO_ELECTRICO,
    "falla electrica": CategoriaDano.DANO_ELECTRICO,
    "falla eléctrica": CategoriaDano.DANO_ELECTRICO,
    # falla_mecanica
    "falla_mecanica": CategoriaDano.FALLA_MECANICA,
    "falla mecanica": CategoriaDano.FALLA_MECANICA,
    "falla mecánica": CategoriaDano.FALLA_MECANICA,
    "motor": CategoriaDano.FALLA_MECANICA,
    "mecanica": CategoriaDano.FALLA_MECANICA,
    "mecánica": CategoriaDano.FALLA_MECANICA,
    "falla motor": CategoriaDano.FALLA_MECANICA,
    "frenos": CategoriaDano.FALLA_MECANICA,
    "transmision": CategoriaDano.FALLA_MECANICA,
    "transmisión": CategoriaDano.FALLA_MECANICA,
    "sobrecalentamiento": CategoriaDano.FALLA_MECANICA,
    "check engine": CategoriaDano.FALLA_MECANICA,
    # suspension
    "suspension": CategoriaDano.SUSPENSION,
    "suspensión": CategoriaDano.SUSPENSION,
    "amortiguador": CategoriaDano.SUSPENSION,
    "amortiguacion": CategoriaDano.SUSPENSION,
    "amortiguación": CategoriaDano.SUSPENSION,
    "tren delantero": CategoriaDano.SUSPENSION,
    "direccion": CategoriaDano.SUSPENSION,
    "dirección": CategoriaDano.SUSPENSION,
    # general
    "general": CategoriaDano.GENERAL,
    "otro": CategoriaDano.GENERAL,
    "otros": CategoriaDano.GENERAL,
}


def _strip_accents(value: str) -> str:
    base = unicodedata.normalize("NFKD", value)
    return "".join(c for c in base if not unicodedata.combining(c))


def normalizar_categoria_dano(raw: str | None) -> CategoriaDano:
    """Convierte texto libre o valor de enum a CategoriaDano normalizado."""
    if not raw:
        return CategoriaDano.GENERAL
    cleaned = _strip_accents(str(raw).strip().lower())
    # Match exacto primero
    match = _CATEGORIA_DANO_ALIASES.get(cleaned)
    if match:
        return match
    # Match por enum value directo
    for member in CategoriaDano:
        if _strip_accents(member.value) == cleaned:
            return member
    # Match parcial — busca el alias más largo que esté contenido en el texto
    best: CategoriaDano | None = None
    best_len = 0
    for alias, categoria in _CATEGORIA_DANO_ALIASES.items():
        alias_clean = _strip_accents(alias)
        if alias_clean in cleaned and len(alias_clean) > best_len:
            best = categoria
            best_len = len(alias_clean)
    return best or CategoriaDano.GENERAL


def try_parse_categoria_dano(raw: str | None) -> CategoriaDano | None:
    if not raw:
        return None
    cleaned = _strip_accents(str(raw).strip().lower())
    match = _CATEGORIA_DANO_ALIASES.get(cleaned)
    if match:
        return match
    for member in CategoriaDano:
        if _strip_accents(member.value) == cleaned:
            return member
    best: CategoriaDano | None = None
    best_len = 0
    for alias, categoria in _CATEGORIA_DANO_ALIASES.items():
        alias_clean = _strip_accents(alias)
        if alias_clean in cleaned and len(alias_clean) > best_len:
            best = categoria
            best_len = len(alias_clean)
    return best


def resolve_categoria_diagnostico(
    *,
    raw: str | None,
    tipo_incidente: str,
    descripcion: str,
) -> CategoriaDano:
    parsed = try_parse_categoria_dano(raw)
    if parsed:
        return parsed
    return normalizar_categoria_dano(" ".join([tipo_incidente or "", descripcion or ""]).strip())
