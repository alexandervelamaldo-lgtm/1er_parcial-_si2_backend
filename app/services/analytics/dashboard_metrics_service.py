"""KPI service del dashboard analítico.

Cada función es PURA: recibe `db` async session + filtros, devuelve un
dataclass/schema con la métrica. NO hace I/O fuera de la DB. NO toca
FastAPI. Esto las hace fáciles de testear con SQLite/SAVEPOINT.

Convenciones:
  - Todos los tiempos retornados en MINUTOS.
  - Si no hay muestras suficientes (n=0) → todos los percentiles None.
  - NUNCA fabricamos valores: si la columna es null o no hay datos, el
    resultado lo refleja con n_muestras=0.
  - Todas las queries usan parámetros (`since`, `until`) — nunca string
    formatting (SQL injection).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.estados_solicitud import EstadoSolicitud
from app.models.solicitudes import Solicitud
from app.models.talleres import Taller
from app.models.tipos_incidente import TipoIncidente
from app.schemas.analytics.dashboard import (
    CancelacionesKPI,
    IncidentesPorTipoItem,
    IncidentesPorTipoKPI,
    SerieTemporalPunto,
    SlaKPI,
    TallerRankingItem,
    TalleresRankingKPI,
    TiempoPromedioKPI,
    ZonaCalienteItem,
    ZonasCalientesKPI,
)
from app.services.analytics.geo_clustering import cluster_incidents
from app.services.analytics.sla_policy import SlaThresholds, get_sla_thresholds


# Estados que indican que la solicitud no se llevó a cabo.
CANCELLED_STATES = {"CANCELADA", "RECHAZADA_TALLER"}


# ── Helpers ────────────────────────────────────────────────────────────


def _to_dt_range(since: date, until: date) -> tuple[datetime, datetime]:
    """Convierte un rango de fechas (inclusive) a rango de datetimes UTC."""
    start = datetime.combine(since, time.min, tzinfo=timezone.utc)
    end = datetime.combine(until, time.max, tzinfo=timezone.utc)
    return start, end


def _normalize_tipo(nombre: str | None) -> str:
    """Mapea el nombre del tipo de incidente a un label canónico."""
    if not nombre:
        return "otros"
    name = nombre.strip().lower()
    if "bater" in name:
        return "bateria"
    if "llant" in name or "neum" in name or "pinch" in name:
        return "llanta"
    if "motor" in name or "mecan" in name or "mecán" in name:
        return "motor"
    if "choque" in name or "colisi" in name or "accidente" in name:
        return "choque"
    return "otros"


# ── K1, K2, K3, K4 — tiempos promedio (avg + p50 + p95) ─────────────────


async def _time_kpi_between(
    db: AsyncSession,
    *,
    since: date,
    until: date,
    taller_id: int | None,
    start_col,
    end_col,
    require_estado_no_cancelado: bool = False,
) -> TiempoPromedioKPI:
    """Calcula avg/p50/p95 de minutos entre dos columnas de fecha.

    Filtra solicitudes en `fecha_solicitud BETWEEN since AND until` con
    ambos timestamps poblados. NUNCA incluye filas con duración negativa
    (datos corruptos los descartamos sin alarmar).
    """
    dt_since, dt_until = _to_dt_range(since, until)
    # Minutos entre las dos columnas: extract epoch / 60.
    delta_minutes = func.extract("epoch", end_col - start_col) / 60.0

    where = [
        Solicitud.fecha_solicitud.between(dt_since, dt_until),
        start_col.is_not(None),
        end_col.is_not(None),
        delta_minutes >= 0,
    ]
    if taller_id is not None:
        where.append(Solicitud.taller_id == taller_id)
    if require_estado_no_cancelado:
        cancel_subq = select(EstadoSolicitud.id).where(
            EstadoSolicitud.nombre.in_(CANCELLED_STATES)
        )
        where.append(~Solicitud.estado_id.in_(cancel_subq))

    stmt = select(
        func.avg(delta_minutes).label("avg_min"),
        func.percentile_cont(0.5).within_group(delta_minutes).label("p50_min"),
        func.percentile_cont(0.95).within_group(delta_minutes).label("p95_min"),
        func.count().label("n"),
    ).where(and_(*where))
    row = (await db.execute(stmt)).one()

    n = int(row.n or 0)
    if n == 0:
        return TiempoPromedioKPI(avg_min=None, p50_min=None, p95_min=None, n_muestras=0)
    return TiempoPromedioKPI(
        avg_min=round(float(row.avg_min), 2) if row.avg_min is not None else None,
        p50_min=round(float(row.p50_min), 2) if row.p50_min is not None else None,
        p95_min=round(float(row.p95_min), 2) if row.p95_min is not None else None,
        n_muestras=n,
    )


async def get_avg_assignment_time(
    db: AsyncSession, *, since: date, until: date, taller_id: int | None = None,
) -> TiempoPromedioKPI:
    """K1: tiempo entre `fecha_solicitud` y `fecha_asignacion`."""
    return await _time_kpi_between(
        db, since=since, until=until, taller_id=taller_id,
        start_col=Solicitud.fecha_solicitud,
        end_col=Solicitud.fecha_asignacion,
    )


async def get_avg_arrival_time(
    db: AsyncSession, *, since: date, until: date, taller_id: int | None = None,
) -> TiempoPromedioKPI:
    """K2: tiempo entre `fecha_asignacion` y `fecha_atencion`."""
    return await _time_kpi_between(
        db, since=since, until=until, taller_id=taller_id,
        start_col=Solicitud.fecha_asignacion,
        end_col=Solicitud.fecha_atencion,
    )


async def get_avg_closure_time(
    db: AsyncSession, *, since: date, until: date, taller_id: int | None = None,
) -> TiempoPromedioKPI:
    """K3: tiempo entre `fecha_atencion` y `fecha_cierre`."""
    return await _time_kpi_between(
        db, since=since, until=until, taller_id=taller_id,
        start_col=Solicitud.fecha_atencion,
        end_col=Solicitud.fecha_cierre,
    )


async def get_end_to_end_time(
    db: AsyncSession, *, since: date, until: date, taller_id: int | None = None,
) -> TiempoPromedioKPI:
    """K4: tiempo end-to-end (`fecha_solicitud` → `fecha_cierre`)."""
    return await _time_kpi_between(
        db, since=since, until=until, taller_id=taller_id,
        start_col=Solicitud.fecha_solicitud,
        end_col=Solicitud.fecha_cierre,
    )


# ── K5 — Incidentes por tipo ────────────────────────────────────────────


async def get_incidents_by_type(
    db: AsyncSession, *, since: date, until: date, taller_id: int | None = None,
) -> IncidentesPorTipoKPI:
    dt_since, dt_until = _to_dt_range(since, until)
    where = [Solicitud.fecha_solicitud.between(dt_since, dt_until)]
    if taller_id is not None:
        where.append(Solicitud.taller_id == taller_id)

    stmt = (
        select(TipoIncidente.nombre.label("nombre"), func.count().label("c"))
        .select_from(Solicitud)
        .join(TipoIncidente, TipoIncidente.id == Solicitud.tipo_incidente_id)
        .where(and_(*where))
        .group_by(TipoIncidente.nombre)
    )
    rows = (await db.execute(stmt)).all()

    # Normalizamos al catálogo canónico (5 buckets), sumando los counts.
    by_canon: Counter = Counter()
    label_for: dict[str, str] = {
        "bateria": "Batería",
        "llanta":  "Llanta",
        "motor":   "Motor",
        "choque":  "Choque",
        "otros":   "Otros",
    }
    for row in rows:
        bucket = _normalize_tipo(row.nombre)
        by_canon[bucket] += int(row.c)

    total = sum(by_canon.values())
    items: list[IncidentesPorTipoItem] = []
    for tipo, count in sorted(by_canon.items(), key=lambda kv: (-kv[1], kv[0])):
        pct = round((count / total) * 100.0, 2) if total > 0 else 0.0
        items.append(
            IncidentesPorTipoItem(
                tipo=tipo, label=label_for.get(tipo, tipo.title()),
                count=count, porcentaje=pct,
            )
        )
    return IncidentesPorTipoKPI(total=total, items=items)


# ── K6 — Talleres más eficientes ────────────────────────────────────────


async def get_top_workshops(
    db: AsyncSession,
    *,
    since: date,
    until: date,
    min_casos: int = 5,
    top_n: int = 10,
) -> TalleresRankingKPI:
    """K6: ranking híbrido (40% llegada + 30% cierre + 20% tasa cierre + 10% rating)."""
    dt_since, dt_until = _to_dt_range(since, until)

    cancel_subq = select(EstadoSolicitud.id).where(
        EstadoSolicitud.nombre.in_(CANCELLED_STATES)
    )

    arrival_min = func.extract("epoch", Solicitud.fecha_atencion - Solicitud.fecha_asignacion) / 60.0
    closure_min = func.extract("epoch", Solicitud.fecha_cierre - Solicitud.fecha_atencion) / 60.0

    stmt = (
        select(
            Taller.id.label("taller_id"),
            Taller.nombre.label("nombre"),
            Taller.rating_promedio.label("rating_promedio"),
            func.avg(arrival_min).label("avg_llegada"),
            func.avg(closure_min).label("avg_cierre"),
            func.count(Solicitud.id).label("casos"),
            func.sum(cast(Solicitud.trabajo_terminado, Integer)).label("completadas"),
        )
        .select_from(Solicitud)
        .join(Taller, Taller.id == Solicitud.taller_id)
        .where(
            and_(
                Solicitud.fecha_solicitud.between(dt_since, dt_until),
                Solicitud.taller_id.is_not(None),
                ~Solicitud.estado_id.in_(cancel_subq),
            )
        )
        .group_by(Taller.id, Taller.nombre, Taller.rating_promedio)
        .having(func.count(Solicitud.id) >= min_casos)
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return TalleresRankingKPI(items=[], min_casos_para_ranking=min_casos)

    # Normalización: invertir tiempos (menor=mejor → mayor=mejor) usando
    # el rango observado. Si todos los talleres tienen el mismo avg,
    # ese factor queda neutro (0.5 a cada uno).
    arrivals = [float(r.avg_llegada) for r in rows if r.avg_llegada is not None]
    closures = [float(r.avg_cierre) for r in rows if r.avg_cierre is not None]
    min_arr, max_arr = (min(arrivals), max(arrivals)) if arrivals else (0.0, 0.0)
    min_clo, max_clo = (min(closures), max(closures)) if closures else (0.0, 0.0)

    def _inv_norm(x: float | None, lo: float, hi: float) -> float:
        if x is None or hi <= lo:
            return 0.5
        return max(0.0, min(1.0, 1.0 - (x - lo) / (hi - lo)))

    items: list[TallerRankingItem] = []
    for r in rows:
        casos = int(r.casos or 0)
        completadas = int(r.completadas or 0)
        tasa = (completadas / casos) if casos > 0 else 0.0
        rating_norm = max(0.0, min(1.0, float(r.rating_promedio or 0.0) / 5.0))
        score = (
            0.40 * _inv_norm(float(r.avg_llegada) if r.avg_llegada is not None else None, min_arr, max_arr)
            + 0.30 * _inv_norm(float(r.avg_cierre) if r.avg_cierre is not None else None, min_clo, max_clo)
            + 0.20 * tasa
            + 0.10 * rating_norm
        )
        items.append(
            TallerRankingItem(
                taller_id=int(r.taller_id),
                nombre=str(r.nombre),
                score=round(score, 3),
                tiempo_promedio_llegada=round(float(r.avg_llegada), 2) if r.avg_llegada is not None else None,
                tiempo_promedio_cierre=round(float(r.avg_cierre), 2) if r.avg_cierre is not None else None,
                casos_atendidos=casos,
                rating_promedio=round(float(r.rating_promedio or 0.0), 2),
                tasa_completadas_pct=round(tasa * 100.0, 2),
            )
        )
    items.sort(key=lambda x: -x.score)
    return TalleresRankingKPI(items=items[:top_n], min_casos_para_ranking=min_casos)


# ── K7 — Zonas con más incidentes ───────────────────────────────────────


async def get_hot_zones(
    db: AsyncSession, *, since: date, until: date, top_n: int = 20,
) -> ZonasCalientesKPI:
    """K7: agrupa solicitudes por celda geográfica (lat/lng a 3 decimales)."""
    dt_since, dt_until = _to_dt_range(since, until)
    stmt = (
        select(
            Solicitud.latitud_incidente,
            Solicitud.longitud_incidente,
            TipoIncidente.nombre,
        )
        .select_from(Solicitud)
        .join(TipoIncidente, TipoIncidente.id == Solicitud.tipo_incidente_id)
        .where(
            and_(
                Solicitud.fecha_solicitud.between(dt_since, dt_until),
                Solicitud.latitud_incidente.is_not(None),
                Solicitud.longitud_incidente.is_not(None),
            )
        )
    )
    rows = (await db.execute(stmt)).all()
    points = [(float(r[0]), float(r[1]), _normalize_tipo(r[2])) for r in rows]
    zones = cluster_incidents(points, top_n=top_n)
    items = [
        ZonaCalienteItem(
            lat=z.lat, lng=z.lng, count=z.count,
            tipo_predominante=z.tipo_predominante,
            tipos_top=z.tipos_top,
        )
        for z in zones
    ]
    return ZonasCalientesKPI(items=items)


# ── K8 — Cancelados / no atendidos ──────────────────────────────────────


async def get_cancellations_breakdown(
    db: AsyncSession, *, since: date, until: date, taller_id: int | None = None,
) -> CancelacionesKPI:
    """K8: total cancelaciones + breakdown por motivo (estado nominal)."""
    dt_since, dt_until = _to_dt_range(since, until)
    where = [Solicitud.fecha_solicitud.between(dt_since, dt_until)]
    if taller_id is not None:
        where.append(Solicitud.taller_id == taller_id)

    total_stmt = select(func.count()).select_from(Solicitud).where(and_(*where))
    total_solicitudes = int((await db.scalar(total_stmt)) or 0)

    cancel_stmt = (
        select(EstadoSolicitud.nombre, func.count().label("c"))
        .select_from(Solicitud)
        .join(EstadoSolicitud, EstadoSolicitud.id == Solicitud.estado_id)
        .where(
            and_(
                *where,
                EstadoSolicitud.nombre.in_(CANCELLED_STATES),
            )
        )
        .group_by(EstadoSolicitud.nombre)
    )
    rows = (await db.execute(cancel_stmt)).all()

    por_motivo: dict[str, int] = {}
    for r in rows:
        estado = str(r.nombre)
        if estado == "CANCELADA":
            por_motivo["cliente_cancelo"] = por_motivo.get("cliente_cancelo", 0) + int(r.c)
        elif estado == "RECHAZADA_TALLER":
            por_motivo["taller_rechazo"] = por_motivo.get("taller_rechazo", 0) + int(r.c)
        else:
            por_motivo["otros"] = por_motivo.get("otros", 0) + int(r.c)

    total_canceladas = sum(por_motivo.values())
    tasa = (total_canceladas / total_solicitudes) * 100.0 if total_solicitudes > 0 else 0.0

    return CancelacionesKPI(
        total_canceladas=total_canceladas,
        total_solicitudes=total_solicitudes,
        tasa_pct=round(tasa, 2),
        por_motivo=por_motivo,
    )


# ── K9 — Cumplimiento SLA ───────────────────────────────────────────────


async def get_sla_compliance(
    db: AsyncSession,
    *,
    since: date,
    until: date,
    taller_id: int | None = None,
    thresholds: SlaThresholds | None = None,
) -> SlaKPI:
    """K9: porcentaje de cumplimiento por etapa + global."""
    th = thresholds or get_sla_thresholds()
    dt_since, dt_until = _to_dt_range(since, until)
    where_base = [Solicitud.fecha_solicitud.between(dt_since, dt_until)]
    if taller_id is not None:
        where_base.append(Solicitud.taller_id == taller_id)

    asign_min = func.extract("epoch", Solicitud.fecha_asignacion - Solicitud.fecha_solicitud) / 60.0
    llegada_min = func.extract("epoch", Solicitud.fecha_atencion - Solicitud.fecha_asignacion) / 60.0
    cierre_min = func.extract("epoch", Solicitud.fecha_cierre - Solicitud.fecha_atencion) / 60.0

    async def _stage(delta_expr, threshold: int, additional_where) -> tuple[float | None, int]:
        stmt = select(
            func.count().label("total"),
            func.sum(func.case((delta_expr <= threshold, 1), else_=0)).label("ok"),
        ).where(and_(*where_base, *additional_where, delta_expr >= 0))
        row = (await db.execute(stmt)).one()
        total = int(row.total or 0)
        ok = int(row.ok or 0)
        pct = (ok / total) * 100.0 if total > 0 else None
        return (round(pct, 2) if pct is not None else None), total

    asignacion_pct, n_asig = await _stage(
        asign_min, th.asignacion_min,
        [Solicitud.fecha_asignacion.is_not(None)],
    )
    llegada_pct, n_lleg = await _stage(
        llegada_min, th.llegada_min,
        [Solicitud.fecha_asignacion.is_not(None), Solicitud.fecha_atencion.is_not(None)],
    )
    cierre_pct, n_cierre = await _stage(
        cierre_min, th.cierre_min,
        [Solicitud.fecha_atencion.is_not(None), Solicitud.fecha_cierre.is_not(None)],
    )

    # Global: cumple los 3, sobre las solicitudes que pasaron las 3 etapas.
    global_stmt = select(
        func.count().label("total"),
        func.sum(
            func.case(
                (
                    and_(
                        asign_min <= th.asignacion_min,
                        llegada_min <= th.llegada_min,
                        cierre_min <= th.cierre_min,
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("ok"),
    ).where(
        and_(
            *where_base,
            Solicitud.fecha_asignacion.is_not(None),
            Solicitud.fecha_atencion.is_not(None),
            Solicitud.fecha_cierre.is_not(None),
            asign_min >= 0, llegada_min >= 0, cierre_min >= 0,
        )
    )
    grow = (await db.execute(global_stmt)).one()
    n_global = int(grow.total or 0)
    ok_global = int(grow.ok or 0)
    global_pct = (ok_global / n_global) * 100.0 if n_global > 0 else None

    return SlaKPI(
        sla_asignacion_pct=asignacion_pct,
        sla_llegada_pct=llegada_pct,
        sla_cierre_pct=cierre_pct,
        sla_global_pct=round(global_pct, 2) if global_pct is not None else None,
        umbrales=th.as_dict(),
        n_evaluadas_asignacion=n_asig,
        n_evaluadas_llegada=n_lleg,
        n_evaluadas_cierre=n_cierre,
        n_evaluadas_global=n_global,
    )


# ── Serie temporal (para gráfico de líneas) ─────────────────────────────


async def get_time_series(
    db: AsyncSession, *, since: date, until: date, taller_id: int | None = None,
) -> list[SerieTemporalPunto]:
    """Devuelve [{fecha, count, por_tipo}] con un punto por día.

    Rellena los días sin solicitudes con count=0 — el frontend espera
    una serie densa para gráficar.
    """
    dt_since, dt_until = _to_dt_range(since, until)
    where = [Solicitud.fecha_solicitud.between(dt_since, dt_until)]
    if taller_id is not None:
        where.append(Solicitud.taller_id == taller_id)

    day = func.date(Solicitud.fecha_solicitud).label("day")
    stmt = (
        select(day, TipoIncidente.nombre, func.count().label("c"))
        .select_from(Solicitud)
        .join(TipoIncidente, TipoIncidente.id == Solicitud.tipo_incidente_id)
        .where(and_(*where))
        .group_by("day", TipoIncidente.nombre)
        .order_by("day")
    )
    rows = (await db.execute(stmt)).all()

    by_day: dict[date, dict[str, int]] = {}
    for r in rows:
        d = r.day if isinstance(r.day, date) else date.fromisoformat(str(r.day))
        bucket = _normalize_tipo(r.nombre)
        by_day.setdefault(d, {})
        by_day[d][bucket] = by_day[d].get(bucket, 0) + int(r.c)

    # Relleno denso: incluye TODOS los días del rango aunque count=0.
    # El frontend grafica una línea continua — un día perdido en el medio
    # rompe el eje X de Chart.js.
    series: list[SerieTemporalPunto] = []
    start_date = dt_since.date()
    end_date = dt_until.date()
    days = (end_date - start_date).days
    for offset in range(days + 1):
        d = start_date + timedelta(days=offset)
        por_tipo = by_day.get(d, {})
        series.append(
            SerieTemporalPunto(
                fecha=d,
                count=sum(por_tipo.values()),
                por_tipo=por_tipo,
            )
        )
    return series
