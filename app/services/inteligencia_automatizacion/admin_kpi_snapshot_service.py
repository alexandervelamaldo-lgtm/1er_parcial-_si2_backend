"""Snapshot administrativo del tenant para el chatbot ejecutivo.

Arma un JSON compacto con los KPIs más relevantes del tenant (totales,
tasas, top clientes/técnicos/talleres, ingresos, incidentes por tipo)
para inyectarlo como contexto en el system prompt del LLM. El LLM lee
el snapshot y responde preguntas del administrador sobre esos datos.

Notas de diseño:
  - **Solo lectura**: no ejecuta acciones ni SQL dinámico, solo agrega.
  - **Compacto**: tope de 5 elementos en cada top; strings acotados.
  - **Sin PII innecesaria**: no incluye emails, teléfonos, ni direcciones.
  - **Best-effort**: si una query falla, el campo queda en None/0 y el
    LLM sabe que ese dato no está disponible.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clientes import Cliente
from app.models.estados_solicitud import EstadoSolicitud
from app.models.pagos import PagoSolicitud
from app.models.solicitudes import Solicitud
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.tipos_incidente import TipoIncidente

logger = logging.getLogger(__name__)

# Solo N elementos por ranking — mantener el prompt corto para el LLM.
_TOP_N = 5


@dataclass
class AdminKpiSnapshot:
    tenant: str
    generado_en: str
    totales: dict = field(default_factory=dict)
    tasas: dict = field(default_factory=dict)
    tiempos_promedio_min: dict = field(default_factory=dict)
    ingresos_ultimos_30d: dict = field(default_factory=dict)
    incidentes_por_tipo: dict = field(default_factory=dict)
    top_clientes: list = field(default_factory=list)
    top_tecnicos: list = field(default_factory=list)
    top_talleres: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


async def _totales(db: AsyncSession) -> tuple[dict, dict]:
    """Cuentas globales + tasas de completado/cancelado."""
    stmt = (
        select(
            EstadoSolicitud.nombre,
            func.count(Solicitud.id),
        )
        .join(EstadoSolicitud, Solicitud.estado_id == EstadoSolicitud.id)
        .group_by(EstadoSolicitud.nombre)
    )
    counts_por_estado: dict[str, int] = {}
    for nombre, cnt in (await db.execute(stmt)).all():
        counts_por_estado[str(nombre).upper()] = int(cnt or 0)

    total = sum(counts_por_estado.values())
    completadas = counts_por_estado.get("COMPLETADA", 0)
    canceladas = counts_por_estado.get("CANCELADA", 0) + counts_por_estado.get("CERRADA", 0)
    activas = total - completadas - canceladas

    clientes_activos = await db.scalar(select(func.count(Cliente.id))) or 0
    talleres_activos = await db.scalar(select(func.count(Taller.id))) or 0
    tecnicos_activos = await db.scalar(select(func.count(Tecnico.id))) or 0

    totales = {
        "solicitudes": total,
        "solicitudes_activas": activas,
        "solicitudes_completadas": completadas,
        "solicitudes_canceladas": canceladas,
        "clientes_registrados": int(clientes_activos),
        "talleres_registrados": int(talleres_activos),
        "tecnicos_registrados": int(tecnicos_activos),
        "por_estado": counts_por_estado,
    }
    tasas = {
        "completados": round(completadas / total, 4) if total else 0.0,
        "cancelacion": round(canceladas / total, 4) if total else 0.0,
    }
    return totales, tasas


async def _tiempos_promedio(db: AsyncSession) -> dict:
    """Tiempos promedio en minutos: asignación, llegada, atención."""
    solicitudes = (
        await db.execute(
            select(
                Solicitud.fecha_solicitud,
                Solicitud.fecha_asignacion,
                Solicitud.fecha_atencion,
                Solicitud.fecha_cierre,
                EstadoSolicitud.nombre,
            )
            .join(EstadoSolicitud, Solicitud.estado_id == EstadoSolicitud.id)
        )
    ).all()

    asignacion, llegada, atencion = [], [], []
    for f_sol, f_asig, f_at, f_cierre, estado in solicitudes:
        if f_asig and f_sol:
            d = (f_asig - f_sol).total_seconds() / 60.0
            if d >= 0:
                asignacion.append(d)
        if f_at and f_asig:
            d = (f_at - f_asig).total_seconds() / 60.0
            if d >= 0:
                llegada.append(d)
        if f_cierre and f_at and str(estado).upper() == "COMPLETADA":
            d = (f_cierre - f_at).total_seconds() / 60.0
            if d >= 0:
                atencion.append(d)

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "asignacion": _avg(asignacion),
        "llegada": _avg(llegada),
        "atencion": _avg(atencion),
    }


async def _ingresos_ultimos_30d(db: AsyncSession) -> dict:
    """Suma de pagos confirmados en los últimos 30 días."""
    desde = datetime.now(timezone.utc) - timedelta(days=30)
    total = await db.scalar(
        select(func.coalesce(func.sum(PagoSolicitud.monto_total), 0))
        .where(PagoSolicitud.estado == "PAGADO")
        .where(PagoSolicitud.fecha_pago >= desde)
    )
    cantidad = await db.scalar(
        select(func.count(PagoSolicitud.id))
        .where(PagoSolicitud.estado == "PAGADO")
        .where(PagoSolicitud.fecha_pago >= desde)
    )
    return {
        "monto_total_bob": float(total or 0),
        "pagos_confirmados": int(cantidad or 0),
        "moneda": "BOB",
    }


async def _incidentes_por_tipo(db: AsyncSession) -> dict:
    stmt = (
        select(TipoIncidente.nombre, func.count(Solicitud.id))
        .join(TipoIncidente, Solicitud.tipo_incidente_id == TipoIncidente.id)
        .group_by(TipoIncidente.nombre)
        .order_by(desc(func.count(Solicitud.id)))
    )
    return {str(nombre): int(cnt) for nombre, cnt in (await db.execute(stmt)).all()}


async def _top_clientes(db: AsyncSession) -> list[dict]:
    """Clientes que más solicitudes generaron (top N)."""
    completadas_case = case((EstadoSolicitud.nombre == "COMPLETADA", 1), else_=0)
    stmt = (
        select(
            Cliente.nombre,
            func.count(Solicitud.id).label("total"),
            func.sum(completadas_case).label("completadas"),
        )
        .join(Cliente, Solicitud.cliente_id == Cliente.id)
        .join(EstadoSolicitud, Solicitud.estado_id == EstadoSolicitud.id)
        .group_by(Cliente.id, Cliente.nombre)
        .order_by(desc("total"))
        .limit(_TOP_N)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "cliente": str(nombre),
            "solicitudes_totales": int(total or 0),
            "solicitudes_completadas": int(completadas or 0),
        }
        for nombre, total, completadas in rows
    ]


async def _top_tecnicos(db: AsyncSession) -> list[dict]:
    """Técnicos con más trabajos completados (top N)."""
    stmt = (
        select(
            Tecnico.nombre,
            func.count(Solicitud.id).label("completados"),
        )
        .join(Tecnico, Solicitud.tecnico_id == Tecnico.id)
        .join(EstadoSolicitud, Solicitud.estado_id == EstadoSolicitud.id)
        .where(EstadoSolicitud.nombre == "COMPLETADA")
        .group_by(Tecnico.id, Tecnico.nombre)
        .order_by(desc("completados"))
        .limit(_TOP_N)
    )
    rows = (await db.execute(stmt)).all()
    return [{"tecnico": str(nombre), "trabajos_completados": int(cnt or 0)} for nombre, cnt in rows]


async def _top_talleres(db: AsyncSession) -> list[dict]:
    """Talleres con mayor volumen — total y completados (top N)."""
    completadas_case = case((EstadoSolicitud.nombre == "COMPLETADA", 1), else_=0)
    stmt = (
        select(
            Taller.nombre,
            func.count(Solicitud.id).label("total"),
            func.sum(completadas_case).label("completadas"),
        )
        .join(Taller, Solicitud.taller_id == Taller.id)
        .join(EstadoSolicitud, Solicitud.estado_id == EstadoSolicitud.id)
        .group_by(Taller.id, Taller.nombre)
        .order_by(desc("total"))
        .limit(_TOP_N)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "taller": str(nombre),
            "solicitudes_totales": int(total or 0),
            "solicitudes_completadas": int(completadas or 0),
            "tasa_completados": round((completadas or 0) / total, 4) if total else 0.0,
        }
        for nombre, total, completadas in rows
    ]


async def _run_safe(coro, fallback):
    """Corre una query; si falla, loggea y devuelve fallback."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin_kpi_snapshot: fallo parcial (%s)", type(exc).__name__)
        return fallback


async def build_admin_snapshot(db: AsyncSession, tenant: str) -> AdminKpiSnapshot:
    """Compone el snapshot completo del tenant. Nunca lanza excepción."""
    snapshot = AdminKpiSnapshot(
        tenant=tenant,
        generado_en=datetime.now(timezone.utc).isoformat(),
    )

    totales_tasas = await _run_safe(_totales(db), ({}, {}))
    snapshot.totales, snapshot.tasas = totales_tasas

    snapshot.tiempos_promedio_min = await _run_safe(_tiempos_promedio(db), {})
    snapshot.ingresos_ultimos_30d = await _run_safe(_ingresos_ultimos_30d(db), {})
    snapshot.incidentes_por_tipo = await _run_safe(_incidentes_por_tipo(db), {})
    snapshot.top_clientes = await _run_safe(_top_clientes(db), [])
    snapshot.top_tecnicos = await _run_safe(_top_tecnicos(db), [])
    snapshot.top_talleres = await _run_safe(_top_talleres(db), [])

    return snapshot
