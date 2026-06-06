"""Agrupa incidentes por celda geográfica para K7 (zonas calientes).

Estrategia: redondeo de lat/lng a 3 decimales (~111m de lado en Santa
Cruz, suficiente para identificar una manzana). Más simple que H3 y no
agrega dependencias.

Si en el futuro el dataset crece y queremos celdas hexagonales reales,
se puede swap a `h3-py` cambiando solo la implementación de `bucket_key`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass


GEO_PRECISION_DECIMALS = 3  # 3 dec ≈ 111m. Cambia a 4 para ~11m.


@dataclass(slots=True)
class HotZone:
    """Una celda geográfica con conteo y tipo predominante."""

    lat: float
    lng: float
    count: int
    tipo_predominante: str | None
    tipos_top: list[tuple[str, int]]  # [(tipo, count), ...] top 3


def bucket_key(lat: float, lng: float) -> tuple[float, float]:
    """Redondea (lat, lng) a la celda geográfica de pertenencia."""
    return (
        round(lat, GEO_PRECISION_DECIMALS),
        round(lng, GEO_PRECISION_DECIMALS),
    )


def cluster_incidents(
    points: list[tuple[float, float, str | None]],
    *,
    top_n: int = 20,
) -> list[HotZone]:
    """Agrupa una lista de incidentes (lat, lng, tipo) en zonas calientes.

    Devuelve hasta `top_n` zonas ordenadas por conteo descendente. Solo
    incluye zonas con al menos 1 incidente (no fabrica celdas vacías).

    El tipo_predominante es el más frecuente dentro de la celda; en caso
    de empate, el primero alfabéticamente (determinismo en tests).
    """
    if not points:
        return []

    by_cell: dict[tuple[float, float], list[str | None]] = defaultdict(list)
    for lat, lng, tipo in points:
        if lat is None or lng is None:
            continue
        try:
            cell = bucket_key(float(lat), float(lng))
        except (TypeError, ValueError):
            continue
        by_cell[cell].append((tipo or "otros").strip().lower() or "otros")

    zones: list[HotZone] = []
    for (lat, lng), tipos in by_cell.items():
        if not tipos:
            continue
        counter = Counter(tipos)
        # Determinismo: orden por (-count, tipo)
        sorted_tipos = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        zones.append(
            HotZone(
                lat=lat,
                lng=lng,
                count=len(tipos),
                tipo_predominante=sorted_tipos[0][0] if sorted_tipos else None,
                tipos_top=sorted_tipos[:3],
            )
        )

    zones.sort(key=lambda z: (-z.count, z.lat, z.lng))
    return zones[:top_n]
