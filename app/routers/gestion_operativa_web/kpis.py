"""
KPI router — operational analytics.

Endpoints:
  GET /kpis/resumen              → full platform KPIs (ADMINISTRADOR / OPERADOR / TALLER)
  GET /kpis/taller/{taller_id}   → single-workshop KPIs (ADMINISTRADOR / OPERADOR / TALLER owner)

Caching:
  Results are cached per (tenant, date_range) for 15 minutes (CACHE_TTL_S).
  A background asyncio.Task invalidates the cache entry when TTL expires.
  The kpi_refresh WebSocket event is broadcast to connected clients when
  the cache is automatically refreshed.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_role_names, require_roles
from app.models.estados_solicitud import EstadoSolicitud
from app.models.solicitudes import Solicitud
from app.models.talleres import Taller
from app.models.tipos_incidente import TipoIncidente
from app.models.users import User
from app.schemas.gestion_operativa_web.kpis import (
    KpiPeriodoItem,
    KpisTallerResumenResponse,
    KpisResumenResponse,
    KpiTallerItem,
    KpiZonaItem,
)
from app.services.realtime_hub import hub

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kpis", tags=["KPIs"])

CACHE_TTL_S = 900  # 15 minutes
_ESTADOS_TERMINALES = {"COMPLETADA", "CANCELADA", "CERRADA"}
_ESTADOS_ACTIVOS_EXCLUSION = {"COMPLETADA", "CANCELADA", "CERRADA"}

# ── In-memory cache ───────────────────────────────────────────────────────────
# Key: (tenant, desde_iso, hasta_iso)   Value: (KpisResumenResponse, expires_at)
_cache: dict[tuple, tuple[KpisResumenResponse | KpisTallerResumenResponse, float]] = {}
_cache_lock = asyncio.Lock()


def _cache_key(tenant: str, desde: datetime | None, hasta: datetime | None) -> tuple:
    return (tenant, desde.isoformat() if desde else "", hasta.isoformat() if hasta else "")


async def _get_cached(key: tuple) -> KpisResumenResponse | KpisTallerResumenResponse | None:
    async with _cache_lock:
        entry = _cache.get(key)
        if entry and asyncio.get_event_loop().time() < entry[1]:
            return entry[0]
        _cache.pop(key, None)
        return None


async def _set_cached(
    key: tuple,
    value: KpisResumenResponse | KpisTallerResumenResponse,
    tenant: str,
) -> None:
    async with _cache_lock:
        _cache[key] = (value, asyncio.get_event_loop().time() + CACHE_TTL_S)
    # Broadcast kpi_refresh to connected WS clients after cache update
    asyncio.get_event_loop().call_later(
        CACHE_TTL_S,
        lambda: asyncio.ensure_future(hub.broadcast_kpi_refresh(tenant)),
    )


async def invalidate_kpi_cache_for_tenant(tenant: str) -> None:
    async with _cache_lock:
        stale_keys = [key for key in _cache if key and key[0] == tenant]
        for key in stale_keys:
            _cache.pop(key, None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _solicitudes_por_dia(solicitudes: Sequence[Solicitud]) -> list[KpiPeriodoItem]:
    """Aggregate solicitudes into daily buckets for the last 30 days."""
    from collections import defaultdict

    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "completados": 0, "cancelados": 0})
    for sol in solicitudes:
        fecha_dt = sol.fecha_solicitud
        if fecha_dt is None:
            continue
        fecha_str = fecha_dt.strftime("%Y-%m-%d")
        buckets[fecha_str]["total"] += 1
        estado_nombre = sol.estado.nombre if sol.estado else ""
        if estado_nombre == "COMPLETADA":
            buckets[fecha_str]["completados"] += 1
        elif estado_nombre in ("CANCELADA", "CERRADA"):
            buckets[fecha_str]["cancelados"] += 1

    return sorted(
        [
            KpiPeriodoItem(
                fecha=fecha,
                total=v["total"],
                completados=v["completados"],
                cancelados=v["cancelados"],
            )
            for fecha, v in buckets.items()
        ],
        key=lambda item: item.fecha,
    )


async def _compute_full_kpis(
    db: AsyncSession,
    tenant: str,
    desde: datetime | None,
    hasta: datetime | None,
    current_user: User,
    roles: set[str],
) -> KpisResumenResponse:
    """Compute KpisResumenResponse from DB — expensive, use cached wrapper."""
    query = (
        select(Solicitud)
        .options(
            selectinload(Solicitud.estado),
            selectinload(Solicitud.tipo_incidente),
            selectinload(Solicitud.taller),
        )
        .join(EstadoSolicitud, Solicitud.estado_id == EstadoSolicitud.id)
        .join(TipoIncidente, Solicitud.tipo_incidente_id == TipoIncidente.id)
    )
    if desde is not None:
        query = query.where(Solicitud.fecha_solicitud >= desde)
    if hasta is not None:
        query = query.where(Solicitud.fecha_solicitud <= hasta)
    # Tenant isolation: TALLER role only sees their own solicitudes
    if "TALLER" in roles and not roles.intersection({"ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR"}):
        result_taller = await db.execute(
            select(Taller).where(Taller.user_id == current_user.id)
        )
        taller = result_taller.scalar_one_or_none()
        if taller:
            query = query.where(Solicitud.taller_id == taller.id)

    result = await db.execute(query)
    solicitudes = result.scalars().all()

    asignacion_values: list[float] = []
    llegada_values: list[float] = []
    atencion_values: list[float] = []
    incidentes_por_tipo: dict[str, int] = {}
    zonas: dict[tuple[float, float], int] = {}
    completadas = 0
    canceladas = 0
    activas = 0

    # Per-taller aggregation
    taller_stats: dict[int, dict] = {}

    for sol in solicitudes:
        estado_nombre = sol.estado.nombre if sol.estado else ""
        tipo_nombre = sol.tipo_incidente.nombre if sol.tipo_incidente else "Sin tipo"

        incidentes_por_tipo[tipo_nombre] = incidentes_por_tipo.get(tipo_nombre, 0) + 1

        if estado_nombre == "COMPLETADA":
            completadas += 1
        elif estado_nombre in ("CANCELADA", "CERRADA"):
            canceladas += 1
        elif estado_nombre not in _ESTADOS_ACTIVOS_EXCLUSION:
            activas += 1

        if sol.latitud_incidente is not None and sol.longitud_incidente is not None:
            lat = round(float(sol.latitud_incidente), 2)
            lng = round(float(sol.longitud_incidente), 2)
            zonas[(lat, lng)] = zonas.get((lat, lng), 0) + 1

        if sol.fecha_asignacion and sol.fecha_solicitud:
            delta = (sol.fecha_asignacion - sol.fecha_solicitud).total_seconds() / 60.0
            if delta >= 0:
                asignacion_values.append(delta)

        if sol.fecha_atencion and sol.fecha_asignacion:
            delta = (sol.fecha_atencion - sol.fecha_asignacion).total_seconds() / 60.0
            if delta >= 0:
                llegada_values.append(delta)

        if sol.fecha_cierre and sol.fecha_atencion and estado_nombre == "COMPLETADA":
            delta = (sol.fecha_cierre - sol.fecha_atencion).total_seconds() / 60.0
            if delta >= 0:
                atencion_values.append(delta)

        # Per-taller aggregation (admin/operator only)
        if sol.taller_id is not None and sol.taller is not None:
            tid = sol.taller_id
            if tid not in taller_stats:
                taller_stats[tid] = {
                    "nombre": sol.taller.nombre,
                    "total": 0,
                    "completados": 0,
                    "atencion": [],
                }
            taller_stats[tid]["total"] += 1
            if estado_nombre == "COMPLETADA":
                taller_stats[tid]["completados"] += 1
            if sol.fecha_cierre and sol.fecha_atencion and estado_nombre == "COMPLETADA":
                delta = (sol.fecha_cierre - sol.fecha_atencion).total_seconds() / 60.0
                if delta >= 0:
                    taller_stats[tid]["atencion"].append(delta)

    total = len(solicitudes)
    zonas_top = sorted(
        (KpiZonaItem(lat=lat, lng=lng, count=count) for (lat, lng), count in zonas.items()),
        key=lambda item: item.count,
        reverse=True,
    )[:5]

    talleres_kpi: list[KpiTallerItem] = []
    if roles.intersection({"ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR"}):
        for tid, stats in sorted(taller_stats.items(), key=lambda x: x[1]["total"], reverse=True):
            talleres_kpi.append(
                KpiTallerItem(
                    taller_id=tid,
                    taller_nombre=stats["nombre"],
                    total_solicitudes=stats["total"],
                    completados=stats["completados"],
                    tiempo_atencion_promedio_min=_avg(stats["atencion"]),
                    tasa_completados=round(stats["completados"] / stats["total"], 4)
                    if stats["total"]
                    else 0.0,
                )
            )

    return KpisResumenResponse(
        tiempo_asignacion_promedio_min=_avg(asignacion_values),
        tiempo_llegada_promedio_min=_avg(llegada_values),
        tiempo_atencion_promedio_min=_avg(atencion_values),
        total_solicitudes=total,
        solicitudes_activas=activas,
        solicitudes_completadas=completadas,
        solicitudes_canceladas=canceladas,
        tasa_completados=round(completadas / total, 4) if total else 0.0,
        tasa_cancelacion=round(canceladas / total, 4) if total else 0.0,
        incidentes_por_tipo=incidentes_por_tipo,
        zonas_top=zonas_top,
        solicitudes_por_dia=_solicitudes_por_dia(solicitudes),
        talleres=talleres_kpi,
        calculado_en=datetime.now(timezone.utc).isoformat(),
        cache_ttl_segundos=CACHE_TTL_S,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/resumen", response_model=KpisResumenResponse)
async def get_kpis_resumen(
    current_user: User = Depends(require_roles("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR", "TALLER")),
    desde: datetime | None = Query(default=None),
    hasta: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> KpisResumenResponse:
    """
    Full platform KPI summary with 15-minute server-side caching.

    - ADMINISTRADOR / OPERADOR see all solicitudes and per-taller breakdown.
    - TALLER sees only their own solicitudes (tenant-isolated).
    """
    from starlette.requests import Request as _Req
    # Resolve tenant from session-info in db (set by get_db middleware)
    tenant: str = db.info.get("tenant_key", "default")
    roles = get_role_names(current_user)
    # TALLER users get tenant-isolated results; include user_id in cache key
    user_scope = current_user.id if "TALLER" in roles and not roles.intersection({"ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR"}) else 0
    key = (*_cache_key(tenant, desde, hasta), user_scope)

    cached = await _get_cached(key)
    if cached and isinstance(cached, KpisResumenResponse):
        return cached

    kpis = await _compute_full_kpis(db, tenant, desde, hasta, current_user, roles)
    await _set_cached(key, kpis, tenant)
    return kpis


@router.get("/taller/{taller_id}", response_model=KpisTallerResumenResponse)
async def get_kpis_taller(
    taller_id: int,
    current_user: User = Depends(require_roles("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR", "TALLER")),
    desde: datetime | None = Query(default=None),
    hasta: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> KpisTallerResumenResponse:
    """
    Workshop-specific KPI summary.

    ADMINISTRADOR/OPERADOR can request any taller_id.
    TALLER role can only access its own taller_id (tenant isolation).
    """
    tenant: str = db.info.get("tenant_key", "default")
    roles = get_role_names(current_user)

    # Authorization: TALLER users can only see their own workshop
    if "TALLER" in roles and "ADMINISTRADOR" not in roles and "OPERADOR" not in roles:
        taller_result = await db.execute(select(Taller).where(Taller.user_id == current_user.id))
        taller = taller_result.scalar_one_or_none()
        if not taller or taller.id != taller_id:
            raise HTTPException(status_code=403, detail="Solo puedes ver los KPIs de tu propio taller")
    else:
        taller_result = await db.execute(select(Taller).where(Taller.id == taller_id))
        taller = taller_result.scalar_one_or_none()
        if not taller:
            raise HTTPException(status_code=404, detail="Taller no encontrado")

    key = ("taller", tenant, str(taller_id), desde.isoformat() if desde else "", hasta.isoformat() if hasta else "")
    cached = await _get_cached(key)
    if cached and isinstance(cached, KpisTallerResumenResponse):
        return cached

    # Query solicitudes for this specific workshop
    query = (
        select(Solicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .join(EstadoSolicitud, Solicitud.estado_id == EstadoSolicitud.id)
        .where(Solicitud.taller_id == taller_id)
    )
    if desde:
        query = query.where(Solicitud.fecha_solicitud >= desde)
    if hasta:
        query = query.where(Solicitud.fecha_solicitud <= hasta)

    result = await db.execute(query)
    solicitudes = result.scalars().all()

    asig, llegada, atencion = [], [], []
    completadas = canceladas = activas = 0
    por_tipo: dict[str, int] = {}

    for sol in solicitudes:
        estado_nombre = sol.estado.nombre if sol.estado else ""
        tipo_nombre = sol.tipo_incidente.nombre if sol.tipo_incidente else "Sin tipo"
        por_tipo[tipo_nombre] = por_tipo.get(tipo_nombre, 0) + 1

        if estado_nombre == "COMPLETADA":
            completadas += 1
        elif estado_nombre in ("CANCELADA", "CERRADA"):
            canceladas += 1
        else:
            activas += 1

        if sol.fecha_asignacion and sol.fecha_solicitud:
            d = (sol.fecha_asignacion - sol.fecha_solicitud).total_seconds() / 60
            if d >= 0:
                asig.append(d)
        if sol.fecha_atencion and sol.fecha_asignacion:
            d = (sol.fecha_atencion - sol.fecha_asignacion).total_seconds() / 60
            if d >= 0:
                llegada.append(d)
        if sol.fecha_cierre and sol.fecha_atencion and estado_nombre == "COMPLETADA":
            d = (sol.fecha_cierre - sol.fecha_atencion).total_seconds() / 60
            if d >= 0:
                atencion.append(d)

    total = len(solicitudes)
    res = KpisTallerResumenResponse(
        taller_id=taller.id,
        taller_nombre=taller.nombre,
        total_solicitudes=total,
        solicitudes_activas=activas,
        solicitudes_completadas=completadas,
        solicitudes_canceladas=canceladas,
        tasa_completados=round(completadas / total, 4) if total else 0.0,
        tasa_cancelacion=round(canceladas / total, 4) if total else 0.0,
        tiempo_asignacion_promedio_min=_avg(asig),
        tiempo_llegada_promedio_min=_avg(llegada),
        tiempo_atencion_promedio_min=_avg(atencion),
        incidentes_por_tipo=por_tipo,
        solicitudes_por_dia=_solicitudes_por_dia(solicitudes),
        calculado_en=datetime.now(timezone.utc).isoformat(),
        cache_ttl_segundos=CACHE_TTL_S,
    )
    await _set_cached(key, res, tenant)
    return res
