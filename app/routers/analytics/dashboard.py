"""Router del dashboard de analítica operacional.

Endpoints:
  - GET /analytics/dashboard                     (ADMIN/OPERADOR)
  - GET /analytics/dashboard/taller/{taller_id}  (TALLER dueño o ADMIN)
  - GET /analytics/incidentes/serie-temporal     (ADMIN/OPERADOR)
  - GET /analytics/zonas/heatmap                 (ADMIN/OPERADOR)

Cache:
  - In-memory TTL 60s segmentado por (tenant_key, endpoint, params).
  - Las solicitudes nuevas no se reflejan en tiempo real — vale la pena
    sacrificar 60s de freshness para evitar martillar la DB con cada
    refresh de dashboard.

Multi-tenant:
  - Confiamos en `get_db` para resolver el tenant. NUNCA filtramos por
    tenant_id en las queries.
  - El cache se segmenta por `db.info["tenant_key"]` para evitar leaks
    cross-tenant.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user, require_roles
from app.dependencies.auth import get_role_names
from app.models.talleres import Taller
from app.models.users import User
from app.schemas.analytics.dashboard import (
    DashboardKPIsResponse,
    HeatmapResponse,
    SerieTemporalResponse,
    TallerDashboardResponse,
    TallerRankingItem,
)
from app.services.analytics.dashboard_metrics_service import (
    get_avg_arrival_time,
    get_avg_assignment_time,
    get_avg_closure_time,
    get_cancellations_breakdown,
    get_end_to_end_time,
    get_hot_zones,
    get_incidents_by_type,
    get_sla_compliance,
    get_time_series,
    get_top_workshops,
)
from app.services.analytics.sla_policy import get_sla_thresholds


router = APIRouter(prefix="/analytics", tags=["Analytics"])


# ── Cache en memoria por tenant ─────────────────────────────────────────


_CACHE_TTL_S = 60.0
_CACHE: dict[tuple[str, str, tuple[Any, ...]], tuple[float, Any]] = {}


def _cache_get(tenant: str, endpoint: str, params: tuple[Any, ...]) -> Any | None:
    key = (tenant, endpoint, params)
    entry = _CACHE.get(key)
    if not entry:
        return None
    expires_at, payload = entry
    if time.monotonic() > expires_at:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_set(tenant: str, endpoint: str, params: tuple[Any, ...], payload: Any) -> None:
    _CACHE[(tenant, endpoint, params)] = (time.monotonic() + _CACHE_TTL_S, payload)


def _default_range() -> tuple[date, date]:
    """Rango por defecto: últimos 30 días (incluyendo hoy)."""
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=30), today


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("/dashboard", response_model=DashboardKPIsResponse)
async def get_full_dashboard(
    since: date | None = Query(None, description="ISO date, default hoy-30d"),
    until: date | None = Query(None, description="ISO date, default hoy"),
    taller_id: int | None = Query(None, description="Filtrar a un solo taller"),
    current_user: User = Depends(require_roles("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> DashboardKPIsResponse:
    """KPIs completos del dashboard administrativo (todos los K1-K9)."""
    if since is None or until is None:
        d_since, d_until = _default_range()
        since = since or d_since
        until = until or d_until
    if since > until:
        raise HTTPException(status_code=400, detail="since debe ser ≤ until")

    tenant = str(db.info.get("tenant_key", "default"))
    cache_params = (since.isoformat(), until.isoformat(), taller_id)
    cached = _cache_get(tenant, "dashboard", cache_params)
    if cached is not None:
        return cached

    t1 = await get_avg_assignment_time(db, since=since, until=until, taller_id=taller_id)
    t2 = await get_avg_arrival_time(db, since=since, until=until, taller_id=taller_id)
    t3 = await get_avg_closure_time(db, since=since, until=until, taller_id=taller_id)
    t4 = await get_end_to_end_time(db, since=since, until=until, taller_id=taller_id)
    incidents = await get_incidents_by_type(db, since=since, until=until, taller_id=taller_id)
    # El ranking de talleres usa el dataset GLOBAL (sin filtrar por taller),
    # incluso cuando el usuario pidió un taller — quiere ver dónde queda
    # su taller en el ranking, no un ranking de uno solo.
    ranking = await get_top_workshops(db, since=since, until=until)
    zones = await get_hot_zones(db, since=since, until=until)
    cancels = await get_cancellations_breakdown(db, since=since, until=until, taller_id=taller_id)
    sla = await get_sla_compliance(db, since=since, until=until, taller_id=taller_id)

    payload = DashboardKPIsResponse(
        desde=since, hasta=until, taller_id_filtro=taller_id,
        generado_en=datetime.now(timezone.utc),
        tiempo_asignacion=t1, tiempo_llegada=t2, tiempo_cierre=t3, tiempo_end_to_end=t4,
        incidentes_por_tipo=incidents,
        talleres_top=ranking,
        zonas_calientes=zones,
        cancelaciones=cancels,
        sla=sla,
    )
    _cache_set(tenant, "dashboard", cache_params, payload)
    return payload


@router.get("/dashboard/taller/{taller_id}", response_model=TallerDashboardResponse)
async def get_taller_dashboard(
    taller_id: int,
    since: date | None = Query(None),
    until: date | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TallerDashboardResponse:
    """Subset enfocado al rol TALLER (debe ser su propio taller) o ADMIN.

    Un taller logueado solo puede ver el dashboard de SU propio taller.
    Un admin/operador puede ver el de cualquier taller. Defense-in-depth:
    re-verificamos en el backend aunque el guard del frontend ya lo haya
    filtrado.
    """
    roles = get_role_names(current_user)
    is_admin = bool(roles.intersection({"ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR"}))
    is_taller = "TALLER" in roles

    if not (is_admin or is_taller):
        raise HTTPException(status_code=403, detail="No tienes permisos para este dashboard")

    taller = await db.get(Taller, taller_id)
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")

    # Si es taller (no admin), debe ser el dueño.
    if is_taller and not is_admin and taller.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Solo puedes ver el dashboard de tu propio taller",
        )

    if since is None or until is None:
        d_since, d_until = _default_range()
        since = since or d_since
        until = until or d_until
    if since > until:
        raise HTTPException(status_code=400, detail="since debe ser ≤ until")

    tenant = str(db.info.get("tenant_key", "default"))
    cache_params = (since.isoformat(), until.isoformat(), taller_id)
    cached = _cache_get(tenant, "dashboard_taller", cache_params)
    if cached is not None:
        return cached

    t_llegada = await get_avg_arrival_time(db, since=since, until=until, taller_id=taller_id)
    t_cierre = await get_avg_closure_time(db, since=since, until=until, taller_id=taller_id)
    t_e2e = await get_end_to_end_time(db, since=since, until=until, taller_id=taller_id)
    cancels = await get_cancellations_breakdown(db, since=since, until=until, taller_id=taller_id)
    sla = await get_sla_compliance(db, since=since, until=until, taller_id=taller_id)

    # Posición global en el ranking (sin filtro taller_id).
    ranking_global = await get_top_workshops(db, since=since, until=until)
    my_ranking: TallerRankingItem | None = next(
        (item for item in ranking_global.items if item.taller_id == taller_id),
        None,
    )

    payload = TallerDashboardResponse(
        desde=since, hasta=until, taller_id=taller_id,
        taller_nombre=taller.nombre,
        generado_en=datetime.now(timezone.utc),
        tiempo_llegada=t_llegada,
        tiempo_cierre=t_cierre,
        tiempo_end_to_end=t_e2e,
        casos_atendidos=cancels.total_solicitudes,
        cancelaciones=cancels,
        sla=sla,
        ranking_global=my_ranking,
    )
    _cache_set(tenant, "dashboard_taller", cache_params, payload)
    return payload


@router.get("/incidentes/serie-temporal", response_model=SerieTemporalResponse)
async def get_incidents_time_series(
    since: date | None = Query(None),
    until: date | None = Query(None),
    taller_id: int | None = Query(None),
    current_user: User = Depends(require_roles("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> SerieTemporalResponse:
    if since is None or until is None:
        d_since, d_until = _default_range()
        since = since or d_since
        until = until or d_until
    if since > until:
        raise HTTPException(status_code=400, detail="since debe ser ≤ until")
    # Restricción del prompt: no más de ~500 puntos por endpoint. 365 días
    # es suficiente para "último año diario"; rangos mayores deben pedir
    # granularidad semanal o mensual (lo dejo para iteración futura).
    if (until - since).days > 365:
        raise HTTPException(status_code=400, detail="Rango máximo 365 días")

    tenant = str(db.info.get("tenant_key", "default"))
    cache_params = (since.isoformat(), until.isoformat(), taller_id)
    cached = _cache_get(tenant, "serie_temporal", cache_params)
    if cached is not None:
        return cached

    puntos = await get_time_series(db, since=since, until=until, taller_id=taller_id)
    payload = SerieTemporalResponse(desde=since, hasta=until, puntos=puntos)
    _cache_set(tenant, "serie_temporal", cache_params, payload)
    return payload


@router.get("/zonas/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    since: date | None = Query(None),
    until: date | None = Query(None),
    top_n: int = Query(50, ge=1, le=500),
    current_user: User = Depends(require_roles("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> HeatmapResponse:
    if since is None or until is None:
        d_since, d_until = _default_range()
        since = since or d_since
        until = until or d_until
    if since > until:
        raise HTTPException(status_code=400, detail="since debe ser ≤ until")

    tenant = str(db.info.get("tenant_key", "default"))
    cache_params = (since.isoformat(), until.isoformat(), top_n)
    cached = _cache_get(tenant, "heatmap", cache_params)
    if cached is not None:
        return cached

    kpi = await get_hot_zones(db, since=since, until=until, top_n=top_n)
    payload = HeatmapResponse(desde=since, hasta=until, items=kpi.items)
    _cache_set(tenant, "heatmap", cache_params, payload)
    return payload
