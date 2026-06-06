"""Tests invariantes sobre route_driving — garantizan que cuando Mapbox
responde con una ruta vial real (varios vértices), el servicio:
  - propaga los vértices sin truncarlos,
  - reporta una distancia > Haversine (un camino vial NO puede ser
    más corto que la línea recta).

Y cuando Mapbox falla, el servicio cae en el fallback Haversine — pero
SIN aparentar que es una ruta real (solo 2 puntos, geometry LineString
de origen→destino).
"""

from __future__ import annotations

import math

import httpx
import pytest
import respx

from app.config import get_settings
from app.services.mapa.mapbox_directions_service import (
    _haversine_km,
    route_driving,
)


def _set_mapbox_token(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("MAPBOX_PUBLIC_TOKEN", "pk.test-FAKE-TOKEN-FOR-RESPX")


def _build_mapbox_response(coords_lng_lat: list[list[float]], distance_m: float, duration_s: float) -> dict:
    """Forma típica de la respuesta de Directions API que importa."""
    return {
        "routes": [
            {
                "distance": distance_m,
                "duration": duration_s,
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords_lng_lat,
                },
                "weight": duration_s,
            },
        ],
        "waypoints": [],
        "code": "Ok",
    }


@pytest.mark.asyncio
async def test_route_driving_preserves_all_vertices_from_mapbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si Mapbox devuelve 12 vértices, el servicio devuelve 12.

    El test pasa coordenadas reales del centro de Santa Cruz (sobre Av.
    Banzer) para que el chequeo simule el escenario de producción.
    """
    _set_mapbox_token(monkeypatch)
    # 12 puntos siguiendo una calle real — el servicio NO debe truncarlos.
    twelve_vertices = [
        [-63.1812, -17.7863],   # origen (Plaza 24 de Septiembre)
        [-63.1813, -17.7855],
        [-63.1814, -17.7845],
        [-63.1815, -17.7838],
        [-63.1817, -17.7830],
        [-63.1819, -17.7822],
        [-63.1821, -17.7815],
        [-63.1824, -17.7806],
        [-63.1827, -17.7795],
        [-63.1830, -17.7785],
        [-63.1832, -17.7775],
        [-63.1835, -17.7765],   # destino
    ]
    async with respx.mock(base_url="https://api.mapbox.com") as router:
        router.get(path__regex=r"/directions/v5/mapbox/driving-traffic/.*").mock(
            return_value=httpx.Response(
                200,
                json=_build_mapbox_response(twelve_vertices, distance_m=2400.0, duration_s=300.0),
            )
        )
        route = await route_driving(
            origen_lat=-17.7863, origen_lon=-63.1812,
            destino_lat=-17.7765, destino_lon=-63.1835,
        )
    assert route.geometry["type"] == "LineString"
    assert len(route.geometry["coordinates"]) == 12, \
        "El servicio no debe truncar la geometría — el frontend depende de los vértices completos."


@pytest.mark.asyncio
async def test_route_driving_distance_is_at_least_1_1x_haversine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariante: una ruta vial real NO puede ser más corta que la línea
    recta (Haversine). Si el resultado violara esto, alguien está
    devolviendo la línea recta como ruta vial — bug crítico."""
    _set_mapbox_token(monkeypatch)
    # Origen y destino reales en Santa Cruz separados ~3.2 km en recta.
    o_lat, o_lon = -17.7863, -63.1812
    d_lat, d_lon = -17.7602, -63.1835
    line_km = _haversine_km(o_lat, o_lon, d_lat, d_lon)
    # Mapbox responde con distance_m que representa 1.3× la recta — típico
    # de una ruta urbana (grilla cuadrada → siempre detour).
    road_km = line_km * 1.3
    route_geom = [
        [o_lon, o_lat],
        [o_lon + 0.001, o_lat + 0.001],
        [o_lon + 0.002, o_lat + 0.003],
        [o_lon + 0.003, o_lat + 0.005],
        [d_lon, d_lat],
    ]
    async with respx.mock(base_url="https://api.mapbox.com") as router:
        router.get(path__regex=r"/directions/v5/mapbox/driving-traffic/.*").mock(
            return_value=httpx.Response(
                200,
                json=_build_mapbox_response(
                    route_geom, distance_m=road_km * 1000.0, duration_s=480.0,
                ),
            )
        )
        route = await route_driving(
            origen_lat=o_lat, origen_lon=o_lon,
            destino_lat=d_lat, destino_lon=d_lon,
        )
    assert route.distance_km >= line_km * 1.1, \
        f"distance_km={route.distance_km} < 1.1× haversine={line_km:.3f} — sospechoso de ser una recta."


@pytest.mark.asyncio
async def test_fallback_haversine_returns_exactly_two_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cuando Mapbox devuelve 422 (NoRoute) en AMBOS perfiles, caemos en el
    fallback Haversine — y la geometría debe tener EXACTAMENTE 2 puntos. Esto
    es la señal que el frontend usa para decidir si confía o no en la
    ruta del backend (≤2 puntos → es fallback, no dibujar como ruta real).

    Mockeamos ambos perfiles (driving-traffic y driving) porque el servicio
    reintenta con driving cuando driving-traffic devuelve 422."""
    _set_mapbox_token(monkeypatch)
    async with respx.mock(base_url="https://api.mapbox.com") as router:
        router.get(path__regex=r"/directions/v5/mapbox/driving(-traffic)?/.*").mock(
            return_value=httpx.Response(422, json={"code": "NoRoute"})
        )
        route = await route_driving(
            origen_lat=-17.7863, origen_lon=-63.1812,
            destino_lat=-17.7602, destino_lon=-63.1835,
        )
    assert route.geometry["type"] == "LineString"
    coords = route.geometry["coordinates"]
    assert len(coords) == 2, "fallback Haversine debe devolver solo origen + destino — 2 puntos."
    # Y el primer punto es el origen, el segundo el destino.
    assert coords[0] == [-63.1812, -17.7863]
    assert coords[1] == [-63.1835, -17.7602]


@pytest.mark.asyncio
async def test_route_driving_does_not_raise_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Robustez: timeout o connection error → caemos en fallback, no
    propagamos excepción (la creación de solicitud no debe fallar por
    Mapbox)."""
    _set_mapbox_token(monkeypatch)
    async with respx.mock(base_url="https://api.mapbox.com") as router:
        router.get(path__regex=r"/directions/v5/mapbox/driving-traffic/.*").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        # No debe lanzar — devuelve fallback.
        route = await route_driving(
            origen_lat=-17.7863, origen_lon=-63.1812,
            destino_lat=-17.7602, destino_lon=-63.1835,
        )
    assert len(route.geometry["coordinates"]) == 2
    assert route.distance_km > 0
