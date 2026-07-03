from pydantic import BaseModel


class KpiZonaItem(BaseModel):
    lat: float
    lng: float
    count: int


class KpiPeriodoItem(BaseModel):
    """Incidents aggregated by calendar day (YYYY-MM-DD)."""
    fecha: str
    total: int
    completados: int
    cancelados: int


class KpiTallerItem(BaseModel):
    """Per-workshop summary — only visible to ADMINISTRADOR/OPERADOR."""
    taller_id: int
    taller_nombre: str
    total_solicitudes: int
    completados: int
    tiempo_atencion_promedio_min: float | None
    tasa_completados: float


class KpisResumenResponse(BaseModel):
    # ── Tiempos promedio ──────────────────────────────────────────────────────
    tiempo_asignacion_promedio_min: float | None
    """Pendiente → Asignada"""
    tiempo_llegada_promedio_min: float | None
    """Asignada → En atención"""
    tiempo_atencion_promedio_min: float | None
    """En atención → Completada"""

    # ── Conteos ───────────────────────────────────────────────────────────────
    total_solicitudes: int
    solicitudes_activas: int
    """Solicitudes en estado no terminal"""
    solicitudes_completadas: int
    solicitudes_canceladas: int

    # ── Tasas ─────────────────────────────────────────────────────────────────
    tasa_completados: float
    """completadas / total"""
    tasa_cancelacion: float
    """canceladas / total"""

    # ── Distribuciones ────────────────────────────────────────────────────────
    incidentes_por_tipo: dict[str, int]
    zonas_top: list[KpiZonaItem]
    solicitudes_por_dia: list[KpiPeriodoItem]
    """Last 30 days, ascending."""

    # ── Por taller (solo ADMINISTRADOR / OPERADOR) ────────────────────────────
    talleres: list[KpiTallerItem] = []

    # ── Meta ──────────────────────────────────────────────────────────────────
    calculado_en: str
    """ISO timestamp when this snapshot was computed."""
    cache_ttl_segundos: int = 900


class KpisTallerResumenResponse(BaseModel):
    """Tenant-scoped summary for a single workshop — used by TALLER role."""
    taller_id: int
    taller_nombre: str
    total_solicitudes: int
    solicitudes_activas: int
    solicitudes_completadas: int
    solicitudes_canceladas: int
    tasa_completados: float
    tasa_cancelacion: float
    tiempo_asignacion_promedio_min: float | None
    tiempo_llegada_promedio_min: float | None
    tiempo_atencion_promedio_min: float | None
    incidentes_por_tipo: dict[str, int]
    solicitudes_por_dia: list[KpiPeriodoItem]
    calculado_en: str
    cache_ttl_segundos: int = 900
