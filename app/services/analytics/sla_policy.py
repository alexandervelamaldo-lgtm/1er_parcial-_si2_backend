"""Políticas de SLA del módulo de analítica operacional.

Los umbrales son configurables vía variables de entorno (con defaults
sanos para el mercado boliviano). Cambiar un umbral NO requiere migrar
DB — solo cambia el cálculo de cumplimiento del próximo refresh del
dashboard.

Definición de cumplimiento por etapa:
  - SLA_ASIGNACION_MIN: tiempo MÁXIMO aceptable entre `fecha_solicitud`
    y `fecha_asignacion`. Una solicitud cumple si t_asignacion ≤ umbral.
  - SLA_LLEGADA_MIN: tiempo MÁXIMO entre `fecha_asignacion` y
    `fecha_atencion`. Aplica solo si la solicitud llegó a EN_ATENCION.
  - SLA_CIERRE_MIN: tiempo MÁXIMO entre `fecha_atencion` y
    `fecha_cierre`. Aplica solo si trabajo_terminado.

Cumplimiento GLOBAL = cumple los 3 (cuando todos aplican).

Si una solicitud aún no llegó a una etapa, esa etapa no se evalúa —
los porcentajes se calculan sobre el subconjunto que SÍ pasó por la
etapa correspondiente. Esto evita penalizar SLA por servicios en curso.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class SlaThresholds:
    """Snapshot inmutable de los umbrales aplicados a una corrida."""

    asignacion_min: int
    llegada_min: int
    cierre_min: int

    def as_dict(self) -> dict[str, int]:
        return {
            "sla_asignacion_min": self.asignacion_min,
            "sla_llegada_min": self.llegada_min,
            "sla_cierre_min": self.cierre_min,
        }


def get_sla_thresholds() -> SlaThresholds:
    """Devuelve los umbrales SLA activos, leyendo env vars o defaults."""
    return SlaThresholds(
        asignacion_min=_int_env("SLA_ASIGNACION_MIN", 15),
        llegada_min=_int_env("SLA_LLEGADA_MIN", 45),
        cierre_min=_int_env("SLA_CIERRE_MIN", 240),
    )


def minutes_between(a, b) -> float | None:
    """Devuelve minutos entre dos datetimes (b - a). None si alguno es None
    o si el resultado es negativo (datos corruptos — no inventamos)."""
    if a is None or b is None:
        return None
    try:
        delta = b - a
    except TypeError:
        return None
    total = delta.total_seconds() / 60.0
    return total if total >= 0 else None
