import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.services.mapa.traffic_calibration import (
    apply_local_traffic_factor,
    compute_eta_range,
)
from app.services.mapa.travel_time_policy import enforce_duration_per_km

logger = logging.getLogger(__name__)

# Overhead empírico para la distancia: Mapbox calcula sobre centerline
# (eje de la calle), no sobre el carril real. Rotondas, U-turns, carriles
# auxiliares y la última cuadra de estacionamiento agregan ~5%.
DISTANCE_OVERHEAD_FACTOR = 1.05


@dataclass(slots=True)
class MapboxRoute:
    distance_km: float
    duration_min: float
    geometry: dict
    # Rango [min, max] del ETA calibrado en minutos. Permite a la UI
    # mostrar "12-18 min" cuando la varianza es alta y "15 min" cuando
    # es baja. Si el caller no lo necesita, ignora el campo.
    duration_range_min: tuple[int, int] = (0, 0)
    # Factor aplicado y etiqueta humana del bin horario — útil para logs
    # y para auditar la calibración a posteriori.
    traffic_factor: float = 1.0
    traffic_label: str = ""


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en km en línea recta sobre la superficie terrestre."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _fallback_route(
    origen_lat: float, origen_lon: float, destino_lat: float, destino_lon: float
) -> MapboxRoute:
    """Cuando Mapbox no encuentra ruta (422 — coordenadas fuera de la red vial,
    típico en datos sembrados de talleres), caemos a una estimación en línea
    recta multiplicada por 1.3 (factor empírico para corregir la distancia de
    vuelo de pájaro vs distancia vial real)."""
    line_km = _haversine_km(origen_lat, origen_lon, destino_lat, destino_lon)
    road_km_raw = line_km * 1.3
    # ~30 km/h promedio urbano → 2 min por km
    travel_check = enforce_duration_per_km(road_km_raw, road_km_raw * 120.0)
    duration_min_raw = travel_check.duration_seconds / 60.0
    # Aplicamos el pipeline de calibración local también al fallback —
    # un cliente nunca debería notar que cayó al fallback por inconsistencia
    # en cómo se reporta el ETA.
    calibrated_min, factor, label = apply_local_traffic_factor(
        duration_min_raw, now=datetime.now(timezone.utc),
    )
    road_km_final = round(road_km_raw * DISTANCE_OVERHEAD_FACTOR, 3)
    lower, upper = compute_eta_range(calibrated_min)
    geometry = {
        "type": "LineString",
        "coordinates": [
            [origen_lon, origen_lat],
            [destino_lon, destino_lat],
        ],
    }
    return MapboxRoute(
        distance_km=road_km_final,
        duration_min=max(1.0, round(calibrated_min, 1)),
        geometry=geometry,
        duration_range_min=(lower, upper),
        traffic_factor=factor,
        traffic_label=label,
    )


async def route_driving(
    *,
    origen_lat: float,
    origen_lon: float,
    destino_lat: float,
    destino_lon: float,
    timeout_s: float = 7.0,
) -> MapboxRoute:
    settings = get_settings()
    token = (settings.mapbox_public_token or "").strip()
    if len(token) < 10:
        raise RuntimeError("MAPBOX_PUBLIC_TOKEN no configurado")

    # Mapbox Directions devuelve 422 ("InvalidInput") de forma intermitente
    # cuando se envían params extra como alternatives/annotations/steps en
    # ciertos tramos urbanos cortos. El set mínimo (geometries+overview) es
    # estable y suficiente: pedimos GeoJSON con la geometría completa.
    params = {
        "access_token": token,
        "geometries": "geojson",
        "overview": "full",
    }
    # `driving-traffic` incorpora tráfico en tiempo real, pero en tramos
    # urbanos cortos responde 422 ("NoRoute") de forma intermitente aunque la
    # calle exista (depende de segmentos de tráfico en vivo). El perfil
    # `driving` no depende de eso y es estable, así que reintentamos con él
    # antes de caer a la recta. La calibración horaria local se aplica igual
    # abajo (ver traffic_calibration.py), así no perdemos realismo de ETA.
    data = None
    for profile in ("driving-traffic", "driving"):
        url = (
            f"https://api.mapbox.com/directions/v5/mapbox/{profile}/"
            f"{origen_lon},{origen_lat};{destino_lon},{destino_lat}"
        )
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            break
        except httpx.HTTPStatusError as exc:
            # 422 en driving-traffic → reintentar con driving (más estable).
            if exc.response.status_code == 422 and profile == "driving-traffic":
                logger.info(
                    "Mapbox driving-traffic 422 para (%s,%s)→(%s,%s) — reintento con driving.",
                    origen_lat, origen_lon, destino_lat, destino_lon,
                )
                continue
            # 404, 429, o 422 también en driving → fallback a línea recta.
            logger.warning(
                "Mapbox Directions (%s) devolvió %s para (%s,%s)→(%s,%s) — usando fallback Haversine.",
                profile, exc.response.status_code, origen_lat, origen_lon, destino_lat, destino_lon,
            )
            return _fallback_route(origen_lat, origen_lon, destino_lat, destino_lon)
        except httpx.HTTPError as exc:
            # Timeout, conexión rota, DNS, etc. — también fallback.
            logger.warning("Mapbox Directions falló por red (%s) — usando fallback Haversine.", exc)
            return _fallback_route(origen_lat, origen_lon, destino_lat, destino_lon)

    if data is None:
        return _fallback_route(origen_lat, origen_lon, destino_lat, destino_lon)

    routes = data.get("routes") if isinstance(data, dict) else None
    if not routes or not isinstance(routes, list) or not isinstance(routes[0], dict):
        logger.warning("Respuesta inválida de Mapbox Directions — usando fallback.")
        return _fallback_route(origen_lat, origen_lon, destino_lat, destino_lon)

    distance_m = float(routes[0].get("distance", 0.0))
    duration_s = float(routes[0].get("duration", 0.0))
    geometry = routes[0].get("geometry")
    if not isinstance(geometry, dict):
        logger.warning("Geometría inválida de Mapbox Directions — usando fallback.")
        return _fallback_route(origen_lat, origen_lon, destino_lat, destino_lon)

    distance_km_raw = distance_m / 1000.0
    travel_check = enforce_duration_per_km(distance_km_raw, duration_s)
    duration_min_raw = travel_check.duration_seconds / 60.0

    # Calibración local Bolivia — corrige el optimismo de Mapbox por
    # hora del día. Usamos UTC now porque traffic_calibration convierte
    # internamente al timezone local (America/La_Paz).
    calibrated_min, factor, label = apply_local_traffic_factor(
        duration_min_raw, now=datetime.now(timezone.utc),
    )
    # Overhead 5% en distancia para reflejar carriles/desvíos reales.
    distance_km_final = round(distance_km_raw * DISTANCE_OVERHEAD_FACTOR, 3)
    lower, upper = compute_eta_range(calibrated_min)

    return MapboxRoute(
        distance_km=distance_km_final,
        duration_min=max(1.0, round(calibrated_min, 1)),
        geometry=geometry,
        duration_range_min=(lower, upper),
        traffic_factor=factor,
        traffic_label=label,
    )
