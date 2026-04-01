from datetime import datetime

from app.models.enums import PrioridadSolicitud


def calcular_prioridad(
    tipo_incidente: str,
    es_carretera: bool,
    condicion_vehiculo: str,
    nivel_riesgo: int,
    fecha_reporte: datetime | None = None,
) -> PrioridadSolicitud:
    puntaje = 0
    fecha_base = fecha_reporte or datetime.now()

    incidentes_criticos = {"Accidente", "Colisión", "Bloqueo de tráfico"}
    incidentes_altos = {"Falla mecánica", "Sin frenos", "Sobrecalentamiento"}

    if tipo_incidente in incidentes_criticos:
        puntaje += 4
    elif tipo_incidente in incidentes_altos:
        puntaje += 3
    else:
        puntaje += 2

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

    if puntaje >= 10:
        return PrioridadSolicitud.CRITICA
    if puntaje >= 8:
        return PrioridadSolicitud.ALTA
    if puntaje >= 5:
        return PrioridadSolicitud.MEDIA
    return PrioridadSolicitud.BAJA
