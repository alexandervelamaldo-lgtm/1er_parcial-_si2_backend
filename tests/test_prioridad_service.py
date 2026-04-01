from app.models.enums import PrioridadSolicitud
from app.services.prioridad_service import calcular_prioridad


def test_prioridad_critica_en_carretera_de_madrugada() -> None:
    prioridad = calcular_prioridad(
        tipo_incidente="Accidente",
        es_carretera=True,
        condicion_vehiculo="Vehículo inmovilizado",
        nivel_riesgo=5,
    )
    assert prioridad == PrioridadSolicitud.CRITICA


def test_prioridad_media_para_incidente_menor() -> None:
    prioridad = calcular_prioridad(
        tipo_incidente="Llanta ponchada",
        es_carretera=False,
        condicion_vehiculo="Operativo con limitaciones",
        nivel_riesgo=2,
    )
    assert prioridad in {PrioridadSolicitud.BAJA, PrioridadSolicitud.MEDIA}
