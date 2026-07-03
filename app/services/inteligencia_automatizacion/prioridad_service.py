from datetime import datetime

from app.models.enums import CategoriaDano, PrioridadSolicitud, normalizar_categoria_dano

# Incrementos de puntaje por categoría de daño
_PUNTAJE_CATEGORIA: dict[CategoriaDano, int] = {
    CategoriaDano.CHOQUE_CARROCERIA: 4,  # Alta severidad estructural
    CategoriaDano.FALLA_MECANICA: 3,     # Motor/transmisión inmoviliza el vehículo
    CategoriaDano.DANO_ELECTRICO: 3,     # Riesgo eléctrico + inmovilización
    CategoriaDano.SUSPENSION: 2,         # Compromete maniobrabilidad
    CategoriaDano.PINCHAZO: 1,           # Manejable con precaución
    CategoriaDano.CHAPERIA_PINTURA: 2,   # Daño estético con riesgo moderado
    CategoriaDano.GENERAL: 1,
}

# Motivo descriptivo para UI — explica "por qué" se asignó esa prioridad
_MOTIVO_CATEGORIA: dict[CategoriaDano, str] = {
    CategoriaDano.CHOQUE_CARROCERIA: "Choque/carrocería: alta prioridad por riesgo estructural.",
    CategoriaDano.FALLA_MECANICA: "Falla mecánica: motor o transmisión comprometidos.",
    CategoriaDano.DANO_ELECTRICO: "Daño eléctrico: riesgo de cortocircuito o incendio.",
    CategoriaDano.SUSPENSION: "Suspensión dañada: compromete la maniobrabilidad.",
    CategoriaDano.PINCHAZO: "Pinchazo: situación controlable con cautela.",
    CategoriaDano.CHAPERIA_PINTURA: "Daño de carrocería/pintura con posible compromiso estructural.",
    CategoriaDano.GENERAL: "Incidente general sin categoría crítica identificada.",
}


def calcular_prioridad(
    tipo_incidente: str,
    es_carretera: bool,
    condicion_vehiculo: str,
    nivel_riesgo: int,
    fecha_reporte: datetime | None = None,
    categoria_dano: str | None = None,
) -> PrioridadSolicitud:
    puntaje = 0
    fecha_base = fecha_reporte or datetime.now()

    # Puntaje por tipo de incidente (texto libre, legado)
    incidentes_criticos = {"Accidente", "Colisión", "Bloqueo de tráfico"}
    incidentes_altos = {"Falla mecánica", "Sin frenos", "Sobrecalentamiento"}

    if tipo_incidente in incidentes_criticos:
        puntaje += 4
    elif tipo_incidente in incidentes_altos:
        puntaje += 3
    else:
        puntaje += 2

    # Puntaje adicional por categoría de daño estandarizada
    cat = normalizar_categoria_dano(categoria_dano)
    puntaje += _PUNTAJE_CATEGORIA.get(cat, 1)

    if es_carretera:
        puntaje += 2

    if 0 <= fecha_base.hour <= 5:
        puntaje += 2

    condicion = condicion_vehiculo.lower()
    if "inmovilizado" in condicion or "no arranca" in condicion:
        puntaje += 2
    elif "limitado" in condicion:
        puntaje += 1

    puntaje += max(0, min(nivel_riesgo, 5))

    if puntaje >= 12:
        return PrioridadSolicitud.CRITICA
    if puntaje >= 9:
        return PrioridadSolicitud.ALTA
    if puntaje >= 6:
        return PrioridadSolicitud.MEDIA
    return PrioridadSolicitud.BAJA


def motivo_categoria_dano(dano_categoria: str | None) -> str | None:
    """Devuelve el texto explicativo de compatibilidad para una categoría de daño.

    Retorna None si no se proporcionó categoría de daño.
    """
    if not dano_categoria:
        return None
    cat = normalizar_categoria_dano(dano_categoria)
    return _MOTIVO_CATEGORIA.get(cat)


def motivo_prioridad(
    tipo_incidente: str,
    es_carretera: bool,
    condicion_vehiculo: str,
    nivel_riesgo: int,
    prioridad: PrioridadSolicitud,
    categoria_dano: str | None = None,
) -> str:
    """Genera texto explicativo del motivo de priorización para mostrar en UI."""
    partes: list[str] = []
    cat = normalizar_categoria_dano(categoria_dano)
    partes.append(_MOTIVO_CATEGORIA.get(cat, ""))
    if es_carretera:
        partes.append("Ubicación en carretera aumenta el riesgo.")
    condicion = condicion_vehiculo.lower()
    if "inmovilizado" in condicion or "no arranca" in condicion:
        partes.append("Vehículo inmovilizado requiere asistencia inmediata.")
    if nivel_riesgo >= 4:
        partes.append(f"Nivel de riesgo declarado: {nivel_riesgo}/5.")
    partes.append(f"Prioridad asignada: {prioridad.value}.")
    return " ".join(p for p in partes if p)
