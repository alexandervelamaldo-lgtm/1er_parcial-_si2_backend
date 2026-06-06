"""Schemas Pydantic del módulo de analítica operacional.

Diseño:
  - Todos los tiempos en MINUTOS (no segundos) para que el frontend no
    tenga que convertir. Si una métrica es null significa "sin muestras
    suficientes para calcular" (no asumir cero).
  - Todos los porcentajes en escala 0-100 (no 0-1).
  - `generado_en` siempre en UTC; el frontend convierte al timezone local.
  - n_muestras se incluye en CADA KPI de tiempo para que la UI pueda
    advertir "muestra pequeña" cuando hay < 10 datos.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


# ── KPIs de tiempo (K1, K2, K3, K4) ─────────────────────────────────────


class TiempoPromedioKPI(BaseModel):
    """Una métrica de tiempo agregada. Si n_muestras=0, todos los
    percentiles vienen en None — NUNCA fabricamos un cero falso."""
    avg_min:    float | None = Field(None, description="Promedio en minutos")
    p50_min:    float | None = Field(None, description="Mediana en minutos")
    p95_min:    float | None = Field(None, description="Percentil 95 en minutos")
    n_muestras: int          = Field(0, description="Tamaño de la muestra usada")
    unidad:     str          = "minutos"


# ── KPI Incidentes por tipo (K5) ────────────────────────────────────────


class IncidentesPorTipoItem(BaseModel):
    tipo:        str
    label:       str
    count:       int
    porcentaje:  float  # 0-100


class IncidentesPorTipoKPI(BaseModel):
    total:    int
    items:    list[IncidentesPorTipoItem]


# ── KPI Talleres más eficientes (K6) ────────────────────────────────────


class TallerRankingItem(BaseModel):
    taller_id:                int
    nombre:                   str
    score:                    float          # 0-1, mayor = mejor
    tiempo_promedio_llegada:  float | None   # minutos
    tiempo_promedio_cierre:   float | None   # minutos
    casos_atendidos:          int
    rating_promedio:          float
    tasa_completadas_pct:     float          # 0-100


class TalleresRankingKPI(BaseModel):
    items:           list[TallerRankingItem]
    min_casos_para_ranking: int = 5


# ── KPI Zonas calientes (K7) ────────────────────────────────────────────


class ZonaCalienteItem(BaseModel):
    lat:                float
    lng:                float
    count:              int
    tipo_predominante:  str | None = None
    tipos_top:          list[tuple[str, int]] = Field(default_factory=list)


class ZonasCalientesKPI(BaseModel):
    items: list[ZonaCalienteItem]


# ── KPI Cancelaciones (K8) ──────────────────────────────────────────────


class CancelacionesKPI(BaseModel):
    total_canceladas:    int
    total_solicitudes:   int
    tasa_pct:            float                 # 0-100
    por_motivo:          dict[str, int]        # "cliente_cancelo", "taller_rechazo", "timeout", "otros"


# ── KPI SLA (K9) ────────────────────────────────────────────────────────


class SlaKPI(BaseModel):
    sla_asignacion_pct:  float | None  # % que cumplieron asignación dentro del umbral
    sla_llegada_pct:     float | None
    sla_cierre_pct:      float | None
    sla_global_pct:      float | None  # cumplen los 3 (solo solicitudes que pasaron por las 3 etapas)
    umbrales:            dict[str, int]
    n_evaluadas_asignacion: int = 0
    n_evaluadas_llegada:    int = 0
    n_evaluadas_cierre:     int = 0
    n_evaluadas_global:     int = 0


# ── Serie temporal de incidentes ────────────────────────────────────────


class SerieTemporalPunto(BaseModel):
    fecha:     date
    count:     int
    por_tipo:  dict[str, int]


# ── Response principal ──────────────────────────────────────────────────


class DashboardKPIsResponse(BaseModel):
    """Payload completo del dashboard administrativo (TODOS los KPIs)."""
    desde:                  date
    hasta:                  date
    taller_id_filtro:       int | None
    generado_en:            datetime           # UTC
    tiempo_asignacion:      TiempoPromedioKPI
    tiempo_llegada:         TiempoPromedioKPI
    tiempo_cierre:          TiempoPromedioKPI
    tiempo_end_to_end:      TiempoPromedioKPI
    incidentes_por_tipo:    IncidentesPorTipoKPI
    talleres_top:           TalleresRankingKPI
    zonas_calientes:        ZonasCalientesKPI
    cancelaciones:          CancelacionesKPI
    sla:                    SlaKPI


class SerieTemporalResponse(BaseModel):
    desde:    date
    hasta:    date
    puntos:   list[SerieTemporalPunto]


class HeatmapResponse(BaseModel):
    desde:    date
    hasta:    date
    items:    list[ZonaCalienteItem]


class TallerDashboardResponse(BaseModel):
    """Subset enfocado al dashboard que ve un usuario rol TALLER."""
    desde:                 date
    hasta:                 date
    taller_id:             int
    taller_nombre:         str
    generado_en:           datetime
    tiempo_llegada:        TiempoPromedioKPI
    tiempo_cierre:         TiempoPromedioKPI
    tiempo_end_to_end:     TiempoPromedioKPI
    casos_atendidos:       int
    cancelaciones:         CancelacionesKPI
    sla:                   SlaKPI
    ranking_global:        TallerRankingItem | None  # su posición actual
