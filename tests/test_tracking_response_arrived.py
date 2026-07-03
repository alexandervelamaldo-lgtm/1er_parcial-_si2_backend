"""Regresión: el seguimiento de un taller SIN técnico que YA llegó al
incidente (estado EN_ATENCION/COMPLETADA) no debe reventar.

Bug original: la rama "llegó" fijaba distancia=0.0 y llamaba a
estimate_eta_minutes(0.0), que exige distancia positiva y lanzaba
TravelTimeRangeError → 500 en GET /solicitudes/{id}/seguimiento → el
detalle móvil/web quedaba en blanco ("No se pudo cargar el detalle").
Llegar significa ETA 0; no se debe invocar al estimador.
"""
from types import SimpleNamespace

import pytest

from app.routers.gestion_solicitudes import solicitudes as S


def _fake_arrived_solicitud(estado: str) -> SimpleNamespace:
    taller = SimpleNamespace(
        nombre="Taller rapidez",
        latitud=-17.782354980062394,
        longitud=-63.18069127273158,
    )
    return SimpleNamespace(
        id=212,
        estado=SimpleNamespace(nombre=estado),
        tecnico=None,
        servicio_demanda=None,
        taller=taller,
        taller_id=42,
        cliente_aprobada=True,
        propuesta_expira_en=None,
        latitud_incidente=-17.7863,
        longitud_incidente=-63.1811983,
        ubicacion_texto="Av. de prueba",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["EN_ATENCION", "COMPLETADA"])
async def test_seguimiento_taller_llegado_no_revienta(
    estado: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Evitar la red: la rama "llegó" fija ETA 0 sin importar la geometría.
    async def _no_route(*_args, **_kwargs):
        return None

    monkeypatch.setattr(S, "_tracking_route_taller_incidente", _no_route)

    resp = await S._build_tracking_response(_fake_arrived_solicitud(estado))

    # No debe lanzar; el equipo ya está sobre el incidente con ETA 0.
    assert resp.estado == estado
    assert resp.tracking_activo is True
    assert resp.distancia_km == 0.0
    assert resp.eta_min == 0
    assert resp.eta_min_lower == 0
    assert resp.eta_min_upper == 0
    assert resp.latitud_actual == pytest.approx(-17.7863)
    assert resp.longitud_actual == pytest.approx(-63.1811983)
