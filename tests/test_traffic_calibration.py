"""Tests del módulo traffic_calibration + integración con route_driving
y travel_time_policy.

Estos tests son **invariantes**, no flaky: usamos datetimes con timezone
explícita para cubrir bins horarios específicos. NUNCA dependemos de
`datetime.now()` real — siempre lo pasamos como argumento.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.config import get_settings
from app.services.mapa.mapbox_directions_service import (
    DISTANCE_OVERHEAD_FACTOR,
    route_driving,
)
from app.services.mapa.traffic_calibration import (
    BASE_FACTOR,
    QUIET_DAY_FACTOR,
    LOCAL_TZ,
    apply_local_traffic_factor,
    compute_eta_range,
    get_traffic_factor,
)
from app.services.mapa.travel_time_policy import (
    MAX_SECONDS_PER_KM,
    estimate_eta_minutes,
    estimate_eta_range_minutes,
)


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Helper para construir un datetime tz-aware en hora de La Paz."""
    return datetime(year, month, day, hour, minute, tzinfo=LOCAL_TZ)


# ── Factor horario (T2, T3, T4 + edge cases) ────────────────────────────


class TestTrafficFactor:
    def test_T2_peak_hour_increases_eta(self):
        """Lunes 18:30 La Paz → factor 1.50 (salida del trabajo)."""
        # 2026-06-01 fue lunes (corroborado contra calendar.weekday()).
        # 2025-12-01 también es lunes.
        monday_evening = _local(2025, 12, 1, 18, 30)
        factor, label = get_traffic_factor(monday_evening)
        assert factor == pytest.approx(1.50)
        assert "salida" in label.lower()

    def test_morning_peak(self):
        """Martes 7:30 → factor 1.45."""
        tuesday_morning = _local(2025, 12, 2, 7, 30)
        factor, _ = get_traffic_factor(tuesday_morning)
        assert factor == pytest.approx(1.45)

    def test_T3_sunday_no_penalty(self):
        """Domingo 14:00 → factor 1.00 (Santa Cruz se vacía)."""
        sunday = _local(2025, 12, 7, 14, 0)  # 2025-12-07 = domingo
        factor, label = get_traffic_factor(sunday)
        assert factor == QUIET_DAY_FACTOR
        assert "domingo" in label.lower()

    def test_T4_default_when_no_time(self):
        """now=None → factor BASE 1.10 (conservador)."""
        factor, _ = get_traffic_factor(None)
        assert factor == BASE_FACTOR

    def test_off_peak_weekday(self):
        """Lunes 10:30 (fuera de pico) → BASE_FACTOR 1.10."""
        monday_offpeak = _local(2025, 12, 1, 10, 30)
        factor, _ = get_traffic_factor(monday_offpeak)
        assert factor == BASE_FACTOR

    def test_saturday_market_peak(self):
        """Sábado 11:00 → factor 1.30 (movimiento de mercado)."""
        saturday = _local(2025, 12, 6, 11, 0)  # 2025-12-06 = sábado
        factor, label = get_traffic_factor(saturday)
        assert factor == pytest.approx(1.30)
        assert "mercado" in label.lower()

    def test_naive_datetime_treated_as_utc(self):
        """Un datetime naive se asume UTC y se convierte a La Paz."""
        # UTC 22:30 = La Paz 18:30 (La Paz es UTC-4) → factor 1.50
        utc_naive = datetime(2025, 12, 1, 22, 30)  # SIN tzinfo
        factor, _ = get_traffic_factor(utc_naive)
        assert factor == pytest.approx(1.50)


# ── apply_local_traffic_factor + ETA mínimo ─────────────────────────────


class TestApplyFactor:
    def test_T5_eta_never_below_one_minute(self):
        """Distancia 0.05 km en hora baja → ETA ≥ 1."""
        # Domingo, factor 1.00
        sunday = _local(2025, 12, 7, 3, 0)
        # 0.05 km × 60 s/km mínimo = 3 segundos → 0.05 min → floor a 1
        calibrated, factor, _ = apply_local_traffic_factor(0.05, now=sunday)
        assert calibrated >= 1.0
        assert factor == 1.00

    def test_peak_hour_calibrated_is_higher(self):
        """Mismo trayecto en pico vs valle → pico ≥ valle."""
        peak = _local(2025, 12, 1, 18, 30)
        valley = _local(2025, 12, 1, 3, 0)
        cal_peak, f_peak, _ = apply_local_traffic_factor(10.0, now=peak)
        cal_valley, f_valley, _ = apply_local_traffic_factor(10.0, now=valley)
        assert cal_peak > cal_valley
        assert f_peak > f_valley


# ── compute_eta_range ───────────────────────────────────────────────────


class TestEtaRange:
    def test_range_is_around_value(self):
        lower, upper = compute_eta_range(20.0)
        # ±15-25% del centro
        assert lower == 17  # 20 * 0.85
        assert upper == 25  # 20 * 1.25
        assert lower < upper

    def test_range_minimum_one(self):
        lower, upper = compute_eta_range(0.5)
        # 0.5 * 0.85 = 0.425 → floor a 1
        assert lower >= 1
        assert upper >= lower

    def test_range_returns_integers(self):
        lower, upper = compute_eta_range(13.7)
        assert isinstance(lower, int)
        assert isinstance(upper, int)


# ── estimate_eta_range_minutes (integración con travel_time_policy) ─────


class TestEstimateEtaRange:
    def test_range_for_typical_5km(self):
        """5 km debe dar un rango realista (no segundos, no horas)."""
        lower, upper = estimate_eta_range_minutes(5.0)
        # Con DEFAULT 103 s/km + factor ≥ 1.0:
        #   5 km × 103 s = 515s = 8.6 min, × 0.85 ≈ 7, × 1.25 ≈ 11
        # En pico × 1.50 → 12.9 min, rango 11..16
        assert 5 <= lower <= 15
        assert lower < upper <= 25


# ── travel_time_policy MAX raised to 240 (T8) ───────────────────────────


class TestPolicyMaxRaised:
    def test_T8_clamp_max_raised_to_240(self):
        assert MAX_SECONDS_PER_KM == 240.0, \
            "MAX_SECONDS_PER_KM debe ser 240 — refleja el tráfico denso real de Santa Cruz en pico."


# ── Integración con route_driving (T1, T6) ──────────────────────────────


def _build_mapbox_response(coords: list, distance_m: float, duration_s: float) -> dict:
    return {
        "routes": [
            {
                "distance": distance_m, "duration": duration_s,
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        ],
        "code": "Ok",
    }


@pytest.mark.asyncio
async def test_T1_driving_traffic_endpoint_is_used(monkeypatch):
    """Verifica que el path contiene `driving-traffic` (no `driving/`)."""
    get_settings.cache_clear()
    monkeypatch.setenv("MAPBOX_PUBLIC_TOKEN", "pk.test-FAKE")

    async with respx.mock(base_url="https://api.mapbox.com") as router:
        route_match = router.get(
            path__regex=r"/directions/v5/mapbox/driving-traffic/.*"
        ).mock(
            return_value=httpx.Response(
                200,
                json=_build_mapbox_response(
                    [[-63.18, -17.78], [-63.181, -17.781], [-63.182, -17.782]],
                    distance_m=1000.0, duration_s=120.0,
                ),
            )
        )
        await route_driving(
            origen_lat=-17.78, origen_lon=-63.18,
            destino_lat=-17.78, destino_lon=-63.18,
        )
        assert route_match.called, "Se debe usar el endpoint driving-traffic"


@pytest.mark.asyncio
async def test_T6_distance_has_5pct_overhead(monkeypatch):
    """Mapbox dice 4.0 km → backend devuelve ≈ 4.2 km (×1.05)."""
    get_settings.cache_clear()
    monkeypatch.setenv("MAPBOX_PUBLIC_TOKEN", "pk.test-FAKE")
    coords = [[-63.18 + 0.001 * i, -17.78 + 0.001 * i] for i in range(10)]
    async with respx.mock(base_url="https://api.mapbox.com") as router:
        router.get(path__regex=r"/directions/v5/mapbox/driving-traffic/.*").mock(
            return_value=httpx.Response(
                200,
                json=_build_mapbox_response(coords, distance_m=4000.0, duration_s=600.0),
            )
        )
        route = await route_driving(
            origen_lat=-17.78, origen_lon=-63.18,
            destino_lat=-17.79, destino_lon=-63.19,
        )
    # 4.0 km × 1.05 = 4.2 km (tolerancia 0.01 por el round a 3 decimales).
    expected = 4.0 * DISTANCE_OVERHEAD_FACTOR
    assert route.distance_km == pytest.approx(expected, abs=0.01)


@pytest.mark.asyncio
async def test_route_includes_range_and_factor(monkeypatch):
    """route_driving debe devolver duration_range_min y traffic_factor."""
    get_settings.cache_clear()
    monkeypatch.setenv("MAPBOX_PUBLIC_TOKEN", "pk.test-FAKE")
    coords = [[-63.18, -17.78], [-63.181, -17.781], [-63.182, -17.782]]
    async with respx.mock(base_url="https://api.mapbox.com") as router:
        router.get(path__regex=r"/directions/v5/mapbox/driving-traffic/.*").mock(
            return_value=httpx.Response(
                200,
                json=_build_mapbox_response(coords, distance_m=2000.0, duration_s=300.0),
            )
        )
        route = await route_driving(
            origen_lat=-17.78, origen_lon=-63.18,
            destino_lat=-17.79, destino_lon=-63.19,
        )
    # El rango debe ser (lower, upper) con lower ≤ upper.
    lower, upper = route.duration_range_min
    assert lower >= 1
    assert lower <= upper
    # El factor aplicado debe ser ≥ 1.00 (nunca descontamos tiempo).
    assert route.traffic_factor >= 1.00
    assert route.traffic_label  # no vacío
