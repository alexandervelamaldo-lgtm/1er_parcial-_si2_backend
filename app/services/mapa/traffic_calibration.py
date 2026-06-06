"""Calibración de ETA al tráfico real de Santa Cruz, Bolivia.

Aún usando el perfil `driving-traffic` de Mapbox, los tiempos quedan
optimistas para el mercado boliviano porque:
  - Mapbox tiene pocos sensores en Santa Cruz comparado con ciudades
    europeas/norteamericanas.
  - El comportamiento de motorizados, micros parados en doble fila y
    semáforos lentos no está modelado.
  - La hora pico real local es más densa que la inferida por Mapbox.

Este módulo aplica un multiplicador adicional según hora del día y día
de la semana. La tabla es estática por ahora; la fase E del plan general
la reemplazará por una versión auto-aprendida con datos reales del
sistema (delta `fecha_atencion - fecha_asignacion`).

Diseño:
  - Stateless. Cada llamada lee la tabla actual — habilitará futuras
    recalibraciones sin tener que reiniciar el proceso.
  - Timezone: America/La_Paz (UTC-4), donde están los usuarios reales.
    Aceptamos `now` como datetime aware o lo asumimos UTC y convertimos.
  - Si `now` es None (jobs, retries, tests sin tiempo) usamos factor
    BASE 1.10 — preferimos ser pesimistas que optimistas.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


# Factor base cuando no podemos identificar el bin horario.
BASE_FACTOR = 1.10
# Domingos / feriados — Santa Cruz se vacía. Sin penalty.
QUIET_DAY_FACTOR = 1.00

# Timezone de operación.
LOCAL_TZ = ZoneInfo("America/La_Paz")


class TrafficWindow(NamedTuple):
    """Una ventana horaria con su factor multiplicador."""
    start: time         # inclusive
    end: time           # exclusive
    factor: float
    label: str


# ── Tabla horaria DEFAULT por día de la semana (0=Lunes, 6=Domingo) ────
# Las ventanas se evalúan en orden — la primera que matchea gana, así que
# las más específicas deben ir antes que las amplias. Si ninguna matchea,
# se usa BASE_FACTOR (cubre noches y madrugadas).

_WEEKDAY_WINDOWS: list[TrafficWindow] = [
    TrafficWindow(time(6, 30), time(9, 0),  1.45, "Entrada al trabajo"),
    TrafficWindow(time(12, 0), time(14, 0), 1.25, "Almuerzo"),
    TrafficWindow(time(17, 30), time(20, 0), 1.50, "Salida del trabajo"),
]

_SATURDAY_WINDOWS: list[TrafficWindow] = [
    TrafficWindow(time(10, 0), time(13, 0), 1.30, "Movimiento de mercado"),
    TrafficWindow(time(17, 0), time(20, 0), 1.20, "Tarde de sábado"),
]

# Domingo: sin penalties activos, todo el día usa QUIET_DAY_FACTOR.
_SUNDAY_WINDOWS: list[TrafficWindow] = []


def _to_local(dt: datetime | None) -> datetime | None:
    """Convierte a hora local de La Paz. Si dt es naive, asumimos UTC.

    Devuelve None si dt es None — el caller debe interpretar como
    "hora desconocida" y usar BASE_FACTOR.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def get_traffic_factor(now: datetime | None = None) -> tuple[float, str]:
    """Devuelve el factor multiplicador y la etiqueta humana del bin horario.

    Args:
        now: timestamp del cálculo. Si es None devolvemos BASE_FACTOR.

    Returns:
        (factor, label) — factor en [1.0, 1.6] aprox., label descriptiva.
    """
    local = _to_local(now)
    if local is None:
        return BASE_FACTOR, "Hora desconocida (factor base conservador)"

    weekday = local.weekday()  # 0=Mon, 6=Sun
    current_time = local.time()

    # Domingo es el caso "calma".
    if weekday == 6:
        return QUIET_DAY_FACTOR, "Domingo (sin tráfico significativo)"

    # Selecciona la tabla apropiada según el día.
    if weekday == 5:
        windows = _SATURDAY_WINDOWS
    else:
        windows = _WEEKDAY_WINDOWS

    for window in windows:
        if window.start <= current_time < window.end:
            return window.factor, window.label

    # Fuera de ventanas pico pero entre semana o sábado → BASE_FACTOR.
    return BASE_FACTOR, "Hora valle (factor base)"


def apply_local_traffic_factor(
    duration_min: float,
    *,
    now: datetime | None = None,
) -> tuple[float, float, str]:
    """Aplica el factor de tráfico local a una duración (en minutos).

    Args:
        duration_min: duración optimista (típicamente de Mapbox driving-
            traffic, ya con tráfico global pero sin calibración local).
        now: timestamp para elegir el bin horario.

    Returns:
        (calibrated_duration_min, factor_applied, label_humana)
        - calibrated_duration_min: tiempo con el factor aplicado.
        - factor_applied: 1.0-1.6 aprox.
        - label_humana: para logging y debugging.
    """
    factor, label = get_traffic_factor(now)
    calibrated = max(1.0, duration_min * factor)
    logger.info(
        "traffic_calibration — factor=%.2f label=%s duration_in=%.1fmin duration_out=%.1fmin",
        factor, label, duration_min, calibrated,
    )
    return calibrated, factor, label


def compute_eta_range(
    duration_min: float,
    *,
    lower_pct: float = 0.85,
    upper_pct: float = 1.25,
) -> tuple[int, int]:
    """Calcula un rango [min, max] alrededor del ETA calibrado.

    El propósito es la honestidad UX: en vez de mostrar "ETA 15 min" como
    promesa, mostrar "ETA 12-18 min" como estimación. La UI decide si
    mostrar rango o número único según la varianza.

    Returns:
        (lower_min, upper_min) — ambos enteros ≥ 1.
    """
    lower = max(1, round(duration_min * lower_pct))
    upper = max(lower, round(duration_min * upper_pct))
    return lower, upper
