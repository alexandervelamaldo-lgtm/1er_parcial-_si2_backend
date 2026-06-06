import pytest

from app.services.mapa.travel_time_policy import (
    MAX_SECONDS_PER_KM,
    MIN_SECONDS_PER_KM,
    TravelTimeRangeError,
    enforce_duration_per_km,
    estimate_eta_minutes,
    verify_duration_per_km,
)


def test_verify_duration_per_km_accepts_values_within_range() -> None:
    check = verify_duration_per_km(distance_km=10, duration_seconds=900)

    assert check.adjusted is False
    assert check.duration_seconds == 900
    assert check.seconds_per_km == 90


def test_verify_duration_per_km_rejects_values_below_minimum() -> None:
    with pytest.raises(TravelTimeRangeError, match="mínimo permitido"):
        verify_duration_per_km(distance_km=2, duration_seconds=60)


def test_verify_duration_per_km_rejects_values_above_maximum() -> None:
    with pytest.raises(TravelTimeRangeError, match="máximo permitido"):
        verify_duration_per_km(distance_km=2, duration_seconds=500)


def test_enforce_duration_per_km_clamps_to_minimum_limit() -> None:
    check = enforce_duration_per_km(distance_km=3, duration_seconds=30)

    assert check.adjusted is True
    assert check.seconds_per_km == MIN_SECONDS_PER_KM
    assert check.duration_seconds == pytest.approx(3 * MIN_SECONDS_PER_KM)
    assert check.reason is not None


def test_enforce_duration_per_km_clamps_to_maximum_limit() -> None:
    check = enforce_duration_per_km(distance_km=1.5, duration_seconds=600)

    assert check.adjusted is True
    assert check.seconds_per_km == MAX_SECONDS_PER_KM
    assert check.duration_seconds == pytest.approx(1.5 * MAX_SECONDS_PER_KM)
    assert check.reason is not None


def test_enforce_duration_per_km_raises_for_invalid_distance() -> None:
    with pytest.raises(TravelTimeRangeError, match="distancia"):
        enforce_duration_per_km(distance_km=0, duration_seconds=120)


def test_estimate_eta_minutes_stays_within_policy_range() -> None:
    eta_min = estimate_eta_minutes(12.0)
    seconds_per_km = (eta_min * 60) / 12.0

    assert MIN_SECONDS_PER_KM <= seconds_per_km <= MAX_SECONDS_PER_KM
