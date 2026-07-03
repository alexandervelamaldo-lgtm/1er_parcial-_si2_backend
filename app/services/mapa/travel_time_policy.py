from dataclasses import dataclass
import math


MIN_SECONDS_PER_KM = 40.0
# Cota MAX subida de 180 a 240 — Santa Cruz hora pico real puede caer
# bajo 15 km/h en avenidas críticas (Av. Cumavi, Banzer al norte, etc.).
# Si bajamos esto, el clamp suprime tiempos legítimos en hora pico y
# el ETA termina siendo más optimista que la realidad.
MAX_SECONDS_PER_KM = 240.0
DEFAULT_SECONDS_PER_KM = 103.0


class TravelTimeRangeError(ValueError):
    """Se lanza cuando una duración por kilómetro cae fuera del rango permitido."""


@dataclass(slots=True)
class TravelTimeCheck:
    duration_seconds: float
    seconds_per_km: float
    adjusted: bool
    reason: str | None = None


def _ensure_positive_finite(value: float, *, label: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise TravelTimeRangeError(f"{label} debe ser un número positivo y finito.")
    return numeric


def verify_duration_per_km(distance_km: float, duration_seconds: float) -> TravelTimeCheck:
    safe_distance = _ensure_positive_finite(distance_km, label="La distancia")
    safe_duration = _ensure_positive_finite(duration_seconds, label="La duración")
    seconds_per_km = safe_duration / safe_distance

    if seconds_per_km < MIN_SECONDS_PER_KM:
        raise TravelTimeRangeError(
            f"La duración por kilómetro ({seconds_per_km:.2f}s/km) está por debajo del mínimo permitido "
            f"de {MIN_SECONDS_PER_KM:.0f}s/km."
        )
    if seconds_per_km > MAX_SECONDS_PER_KM:
        raise TravelTimeRangeError(
            f"La duración por kilómetro ({seconds_per_km:.2f}s/km) supera el máximo permitido "
            f"de {MAX_SECONDS_PER_KM:.0f}s/km."
        )

    return TravelTimeCheck(
        duration_seconds=safe_duration,
        seconds_per_km=seconds_per_km,
        adjusted=False,
    )


def enforce_duration_per_km(distance_km: float, duration_seconds: float) -> TravelTimeCheck:
    safe_distance = _ensure_positive_finite(distance_km, label="La distancia")
    safe_duration = _ensure_positive_finite(duration_seconds, label="La duración")

    try:
        return verify_duration_per_km(safe_distance, safe_duration)
    except TravelTimeRangeError as exc:
        bounded_seconds_per_km = min(
            max(safe_duration / safe_distance, MIN_SECONDS_PER_KM),
            MAX_SECONDS_PER_KM,
        )
        adjusted_duration = bounded_seconds_per_km * safe_distance
        return TravelTimeCheck(
            duration_seconds=adjusted_duration,
            seconds_per_km=bounded_seconds_per_km,
            adjusted=True,
            reason=str(exc),
        )


def estimate_eta_minutes(distance_km: float, *, default_seconds_per_km: float = DEFAULT_SECONDS_PER_KM) -> int:
    """ETA en minutos (entero, mínimo 1) aplicando calibración local horaria.

    Importa el factor de tráfico de Bolivia desde `traffic_calibration`
    para que el seguimiento del técnico use el mismo modelo que
    `route_driving` — UI consistente.
    """
    # Import local para evitar ciclo con mapbox_directions_service que
    # importa este módulo.
    from datetime import datetime, timezone
    from app.services.mapa.traffic_calibration import apply_local_traffic_factor

    check = enforce_duration_per_km(
        distance_km,
        _ensure_positive_finite(distance_km, label="La distancia") * default_seconds_per_km,
    )
    raw_min = check.duration_seconds / 60.0
    calibrated_min, _factor, _label = apply_local_traffic_factor(
        raw_min, now=datetime.now(timezone.utc),
    )
    return max(1, round(calibrated_min))


def estimate_eta_range_minutes(distance_km: float, *, default_seconds_per_km: float = DEFAULT_SECONDS_PER_KM) -> tuple[int, int]:
    """Igual que estimate_eta_minutes pero devuelve (lower, upper).

    Mantenemos esta función separada de `estimate_eta_minutes` para no
    romper los callers existentes — quienes quieran el rango la usan
    explícitamente."""
    from datetime import datetime, timezone
    from app.services.mapa.traffic_calibration import (
        apply_local_traffic_factor,
        compute_eta_range,
    )

    check = enforce_duration_per_km(
        distance_km,
        _ensure_positive_finite(distance_km, label="La distancia") * default_seconds_per_km,
    )
    raw_min = check.duration_seconds / 60.0
    calibrated_min, _factor, _label = apply_local_traffic_factor(
        raw_min, now=datetime.now(timezone.utc),
    )
    return compute_eta_range(calibrated_min)
