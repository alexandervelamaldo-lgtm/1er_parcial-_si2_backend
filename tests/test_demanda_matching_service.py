from datetime import datetime, timezone

from app.models.enums import PrioridadSolicitud
from app.models.solicitudes import Solicitud
from app.models.tecnicos import Tecnico
from app.services.gestion_operativa_web.demanda_matching_service import (
    calcular_match_tecnico,
    especialidad_compatible,
    tecnico_operativamente_disponible,
)


def _build_request(categoria_dano: str = "llantas") -> Solicitud:
    return Solicitud(
        cliente_id=1,
        vehiculo_id=1,
        tipo_incidente_id=1,
        estado_id=1,
        latitud_incidente=-17.7833,
        longitud_incidente=-63.1821,
        descripcion="Vehiculo con llanta pinchada",
        condicion_vehiculo="Inmovilizado",
        nivel_riesgo=2,
        prioridad=PrioridadSolicitud.MEDIA,
        categoria_dano=categoria_dano,
    )


def _build_tecnico(**kwargs) -> Tecnico:
    base = {
        "id": 10,
        "user_id": 10,
        "nombre": "Carlos Roca",
        "telefono": "70000001",
        "especialidad": "Llantas y alineacion",
        "latitud_actual": -17.7840,
        "longitud_actual": -63.1810,
        "disponibilidad": True,
        "radio_cobertura_km": 20.0,
        "en_turno": True,
        "ubicacion_actualizada_en": datetime.now(timezone.utc),
    }
    base.update(kwargs)
    return Tecnico(**base)


def test_especialidad_compatible_por_categoria() -> None:
    assert especialidad_compatible("llantas", "Servicio de llantas y neumáticos")
    assert especialidad_compatible("electricidad", "Diagnóstico eléctrico")
    assert not especialidad_compatible("motor", "Polarizado y audio")


def test_tecnico_operativamente_disponible_requiere_turno_y_disponibilidad() -> None:
    assert tecnico_operativamente_disponible(_build_tecnico())
    assert not tecnico_operativamente_disponible(_build_tecnico(disponibilidad=False))
    assert not tecnico_operativamente_disponible(_build_tecnico(en_turno=False))


def test_match_tecnico_retorna_match_con_cobertura_valida() -> None:
    match = calcular_match_tecnico(_build_request("llantas"), _build_tecnico(), max_radio_km=25)

    assert match is not None
    assert match.match_especialidad is True
    assert match.cobertura_valida is True
    assert match.distancia_km < 1
    assert match.score > 40


def test_match_tecnico_descarta_fuera_de_cobertura() -> None:
    match = calcular_match_tecnico(
        _build_request("motor"),
        _build_tecnico(latitud_actual=-17.5, longitud_actual=-63.0, radio_cobertura_km=5),
        max_radio_km=25,
    )

    assert match is None


def test_match_tecnico_tolera_especialidad_no_exacta_si_hay_cercania() -> None:
    match = calcular_match_tecnico(
        _build_request("motor"),
        _build_tecnico(especialidad="Mecanica general"),
        max_radio_km=25,
    )

    assert match is not None
    assert match.match_especialidad is True
