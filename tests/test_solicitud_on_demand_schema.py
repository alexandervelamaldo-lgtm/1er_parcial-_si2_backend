from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.gestion_solicitudes.solicitudes import SolicitudCreate, SolicitudTrabajoFinalizadoRequest


def test_solicitud_create_exige_geolocalizacion_valida_de_cliente_y_servicio() -> None:
    payload = SolicitudCreate(
        cliente_id=1,
        vehiculo_id=1,
        tipo_incidente_id=1,
        latitud_incidente=-17.78,
        longitud_incidente=-63.18,
        latitud_cliente=-17.79,
        longitud_cliente=-63.17,
        descripcion="Falla mecanica en plena avenida, el vehiculo no enciende",
        danos_descripcion="No arranca y sale humo del motor",
        fecha_incidente=datetime.now(timezone.utc),
        ubicacion_texto="Av. Banzer 3er anillo",
        categoria_dano="motor",
    )

    assert payload.latitud_cliente == -17.79
    assert payload.longitud_incidente == -63.18


def test_solicitud_create_rechaza_coordenadas_fuera_de_rango() -> None:
    with pytest.raises(ValidationError):
        SolicitudCreate(
            cliente_id=1,
            vehiculo_id=1,
            tipo_incidente_id=1,
            latitud_incidente=-91,
            longitud_incidente=-63.18,
            latitud_cliente=-17.79,
            longitud_cliente=-63.17,
            descripcion="Descripcion suficientemente larga para validar la solicitud",
            danos_descripcion="Daño suficientemente descriptivo",
            categoria_dano="general",
        )


def test_trabajo_finalizado_requiere_confirmacion_ubicacion() -> None:
    payload = SolicitudTrabajoFinalizadoRequest(
        costo_final=450,
        observacion="Se sustituyo la llanta y se verifico la presion final",
        latitud_confirmacion=-17.78,
        longitud_confirmacion=-63.18,
    )

    assert payload.latitud_confirmacion == -17.78
    assert payload.longitud_confirmacion == -63.18


def test_trabajo_finalizado_rechaza_longitud_invalida() -> None:
    with pytest.raises(ValidationError):
        SolicitudTrabajoFinalizadoRequest(
            costo_final=450,
            observacion="Observacion valida para cierre tecnico del servicio",
            latitud_confirmacion=-17.78,
            longitud_confirmacion=-181,
        )
