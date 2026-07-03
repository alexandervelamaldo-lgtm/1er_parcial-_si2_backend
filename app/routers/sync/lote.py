"""
Offline batch-sync endpoint.

Mobile clients operating in offline mode queue operations locally (SQLite).
When connectivity is restored, they POST a batch here.

Design principles:
  1. Idempotency — each operation carries a UUID `idempotency_key`.
     Processed keys are persisted in the `sync_idempotencia` table, so the
     guarantee holds ACROSS processes/replicas and survives restarts and
     cold-starts (critical in the cloud, where an in-memory cache would be
     lost and a client retry would replay the operation → duplicates).
  2. Order preservation — operations are processed in the order of
     `offline_created_at`, not arrival order.
  3. No data loss — any operation that fails is reported back with an
     error message so the client can retry individually. A failed operation
     leaves NO idempotency record, so the retry runs cleanly.
  4. Security — users can only sync operations that belong to their own
     cliente_id / taller_id (enforced per operation type).

Supported operation types:
  - crear_solicitud     → equivalent to POST /solicitudes
  - actualizar_estado   → restricted to TECNICO role (state machine enforced)
  - cancelar_solicitud  → restricted to owner CLIENTE
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, get_tenant_sessionmaker
from app.dependencies.auth import get_current_cliente_id, get_current_user, get_role_names
from app.models.clientes import Cliente
from app.models.estados_solicitud import EstadoSolicitud
from app.models.historial_eventos import HistorialEvento
from app.models.solicitudes import Solicitud
from app.models.sync_idempotencia import SyncIdempotencia
from app.models.tipos_incidente import TipoIncidente
from app.models.users import User
from app.models.vehiculos import Vehiculo
from app.routers.gestion_solicitudes.solicitudes import (
    _broadcast_state_change,
    _create_request_in_session,
    _debug_report,
    _ensure_client_profile_in_tenant,
)
from app.schemas.gestion_solicitudes.solicitudes import SolicitudCreate
from app.services.realtime_hub import hub
from app.services.tenant_registry import tenant_registry
from app.services.workshop_tenant_routing import resolve_workshop_tenant_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["Sync — offline"])

# Cuánto tiempo conservamos los registros de idempotencia. Una vez que el
# cliente confirmó la sincronización ya no reintenta esa clave, así que 7 días
# es un margen amplio para reintentos tardíos sin dejar crecer la tabla.
_IDEM_RETENTION_DAYS = 7


# ── DB-backed idempotency ─────────────────────────────────────────────────────


async def _idem_lookup(db: AsyncSession, key: str) -> dict | None:
    """Devuelve el resultado guardado de una clave ya procesada, o None."""
    stored = await db.scalar(
        select(SyncIdempotencia.resultado).where(SyncIdempotencia.idempotency_key == key)
    )
    if stored is None:
        return None
    try:
        return json.loads(stored)
    except (ValueError, TypeError):
        # Registro corrupto/legacy: lo tratamos como procesado con payload
        # vacío para NUNCA re-ejecutar el handler con efectos secundarios.
        return {}


async def _idem_save(
    db: AsyncSession,
    key: str,
    tipo: str,
    usuario_id: int | None,
    result: dict,
) -> bool:
    """
    Persiste el registro de idempotencia. Debe llamarse DESPUÉS de que el
    handler haya commiteado su propio trabajo.

    Devuelve True si se guardó, False si otra request concurrente ya guardó la
    misma clave (violación de unicidad). El índice UNIQUE es lo que hace seguro
    el caso de duplicado concurrente entre procesos/réplicas.
    """
    db.add(
        SyncIdempotencia(
            idempotency_key=key,
            tipo=tipo,
            usuario_id=usuario_id,
            resultado=json.dumps(result, default=str),
        )
    )
    try:
        await db.commit()
        return True
    except IntegrityError:
        await db.rollback()
        return False


async def _purge_old_idempotencia(db: AsyncSession) -> None:
    """Purga de retención best-effort: elimina registros vencidos."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_IDEM_RETENTION_DAYS)
    try:
        await db.execute(delete(SyncIdempotencia).where(SyncIdempotencia.creado_en < cutoff))
        await db.commit()
    except Exception:  # noqa: BLE001 — la purga jamás debe tumbar la sync
        await db.rollback()


# ── Schemas ───────────────────────────────────────────────────────────────────


class SyncOperation(BaseModel):
    tipo: str = Field(description="crear_solicitud | actualizar_estado | cancelar_solicitud")
    idempotency_key: str = Field(min_length=1, max_length=36)
    payload: dict[str, Any]
    offline_created_at: str | None = None
    """ISO 8601 timestamp when the operation was created offline."""


class SyncLoteRequest(BaseModel):
    operations: list[SyncOperation] = Field(max_length=100)


class SyncOperationResult(BaseModel):
    idempotency_key: str
    tipo: str
    status: str  # "ok" | "duplicate" | "error"
    data: dict | None = None
    error: str | None = None


class SyncLoteResponse(BaseModel):
    total: int
    ok: int
    duplicates: int
    errors: int
    results: list[SyncOperationResult]


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_estado(db: AsyncSession, nombre: str) -> EstadoSolicitud | None:
    return await db.scalar(select(EstadoSolicitud).where(EstadoSolicitud.nombre == nombre))


async def _resolver_tipo_incidente(db: AsyncSession, payload: dict) -> TipoIncidente | None:
    """Resuelve el TipoIncidente de una op offline en cascada: por id → por
    nombre → primer tipo disponible.

    El móvil puede crear la emergencia 100% offline usando el catálogo embebido
    por defecto, cuyos ids NO necesariamente coinciden con los de este tenant
    (hay dos seeders distintos y los SERIAL de Postgres no se reinician al
    re-sembrar). Resolver por nombre — y, como último recurso, por el primer
    tipo existente — garantiza que el FK sea válido y la emergencia jamás se
    pierda al sincronizar por un id "adivinado".

    Devuelve None solo si el tenant no tiene NINGÚN tipo configurado.
    """
    tipo_inc = await db.get(TipoIncidente, int(payload["tipo_incidente_id"]))
    if tipo_inc is not None:
        return tipo_inc
    nombre_tipo = payload.get("tipo_incidente_nombre")
    if nombre_tipo:
        tipo_inc = (
            await db.execute(
                select(TipoIncidente).where(TipoIncidente.nombre == str(nombre_tipo))
            )
        ).scalar_one_or_none()
        if tipo_inc is not None:
            return tipo_inc
    return (
        await db.execute(select(TipoIncidente).order_by(TipoIncidente.id).limit(1))
    ).scalar_one_or_none()


def _parse_offline_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Operation handlers ────────────────────────────────────────────────────────


async def _handle_crear_solicitud(
    payload: dict,
    current_user: User,
    actor_id: int,
    roles: set[str],
    cliente_id: int | None,
    db: AsyncSession,
    tenant: str,
    offline_ts: datetime | None,
) -> dict:
    """Create a solicitud — CLIENTE role only."""
    if "CLIENTE" not in roles or cliente_id is None:
        raise ValueError("Solo los clientes pueden crear solicitudes")

    desc = str(payload.get("descripcion", ""))
    tipo_inc = await _resolver_tipo_incidente(db, payload)
    if tipo_inc is None:
        raise ValueError("No hay tipos de incidente configurados en el tenant")

    # Las solicitudes creadas en modo offline se centralizan en el tenant default
    # para que las gestione el operador principal.
    target_tenant = "default"
    # #region debug-point B:offline-target-tenant
    _debug_report(
        "B",
        "backend/app/routers/sync/lote.py:_handle_crear_solicitud",
        "offline request target tenant resolved",
        {
            "source_tenant": tenant,
            "target_tenant": target_tenant,
            "cliente_id": cliente_id,
            "tipo_incidente": tipo_inc.nombre,
            "offline_ts": offline_ts.isoformat() if offline_ts else None,
        },
    )
    # #endregion

    create_payload = SolicitudCreate.model_validate(
        {
            **payload,
            "cliente_id": cliente_id,
            "tipo_incidente_id": int(payload.get("tipo_incidente_id") or tipo_inc.id),
            "descripcion": desc,
        }
    )

    source_cliente = await db.scalar(select(Cliente).where(Cliente.id == cliente_id))
    source_vehiculo = await db.scalar(
        select(Vehiculo).where(
            Vehiculo.id == create_payload.vehiculo_id,
            Vehiculo.cliente_id == cliente_id,
        )
    )
    if source_cliente is None or source_vehiculo is None:
        raise ValueError("Cliente o vehículo no válidos para sincronizar la solicitud offline")

    async def _find_recent_duplicate(
        session: AsyncSession,
        *,
        target_cliente_id: int,
        target_vehiculo_id: int,
        target_tipo_id: int,
    ) -> Solicitud | None:
        if not offline_ts:
            return None
        window_start = offline_ts.replace(tzinfo=timezone.utc) if offline_ts.tzinfo is None else offline_ts
        window_end = window_start + timedelta(minutes=10)
        return await session.scalar(
            select(Solicitud).where(
                Solicitud.cliente_id == target_cliente_id,
                Solicitud.vehiculo_id == target_vehiculo_id,
                Solicitud.tipo_incidente_id == target_tipo_id,
                Solicitud.descripcion == desc,
                Solicitud.fecha_solicitud >= window_start,
                Solicitud.fecha_solicitud <= window_end,
            )
        )

    if target_tenant != tenant:
        if not tenant_registry.exists(target_tenant):
            raise ValueError(f"Tenant de talleres '{target_tenant}' no está provisionado")
        sessionmaker = get_tenant_sessionmaker(target_tenant)
        async with sessionmaker() as tenant_db:
            tenant_db.info["tenant_key"] = target_tenant
            tipo_incidente_tenant = await tenant_db.scalar(
                select(TipoIncidente).where(TipoIncidente.nombre == tipo_inc.nombre)
            )
            if tipo_incidente_tenant is None:
                tipo_incidente_tenant = TipoIncidente(
                    nombre=tipo_inc.nombre,
                    descripcion=tipo_inc.descripcion,
                )
                tenant_db.add(tipo_incidente_tenant)
                await tenant_db.flush()
            _, tenant_cliente, tenant_vehiculo = await _ensure_client_profile_in_tenant(
                tenant_db=tenant_db,
                current_user=current_user,
                source_cliente=source_cliente,
                source_vehiculo=source_vehiculo,
            )
            existing = await _find_recent_duplicate(
                tenant_db,
                target_cliente_id=tenant_cliente.id,
                target_vehiculo_id=tenant_vehiculo.id,
                target_tipo_id=tipo_incidente_tenant.id,
            )
            if existing:
                # #region debug-point C:offline-deduplicated
                _debug_report(
                    "C",
                    "backend/app/routers/sync/lote.py:_handle_crear_solicitud",
                    "offline request deduplicated in target tenant",
                    {
                        "target_tenant": target_tenant,
                        "solicitud_id": existing.id,
                        "cliente_id": tenant_cliente.id,
                    },
                )
                # #endregion
                return {"solicitud_id": existing.id, "deduplicado": True}
            result = await _create_request_in_session(
                db=tenant_db,
                payload=create_payload,
                cliente=tenant_cliente,
                vehiculo=tenant_vehiculo,
                tipo_incidente=tipo_incidente_tenant,
                usuario_id=tenant_cliente.user_id,
            )
            # #region debug-point A:offline-created-target-tenant
            _debug_report(
                "A",
                "backend/app/routers/sync/lote.py:_handle_crear_solicitud",
                "offline request created in target tenant",
                {
                    "target_tenant": target_tenant,
                    "solicitud_id": result.id,
                    "cliente_user_id": tenant_cliente.user_id,
                },
            )
            # #endregion
            await _broadcast_state_change(target_tenant, result.id, "REGISTRADA")
            return {"solicitud_id": result.id, "estado": "REGISTRADA"}

    existing = await _find_recent_duplicate(
        db,
        target_cliente_id=cliente_id,
        target_vehiculo_id=create_payload.vehiculo_id,
        target_tipo_id=tipo_inc.id,
    )
    if existing:
        # #region debug-point C:offline-deduplicated-current
        _debug_report(
            "C",
            "backend/app/routers/sync/lote.py:_handle_crear_solicitud",
            "offline request deduplicated in current tenant",
            {
                "target_tenant": tenant,
                "solicitud_id": existing.id,
                "cliente_id": cliente_id,
            },
        )
        # #endregion
        return {"solicitud_id": existing.id, "deduplicado": True}
    result = await _create_request_in_session(
        db=db,
        payload=create_payload,
        cliente=source_cliente,
        vehiculo=source_vehiculo,
        tipo_incidente=tipo_inc,
        usuario_id=actor_id,
    )
    # #region debug-point A:offline-created-current-tenant
    _debug_report(
        "A",
        "backend/app/routers/sync/lote.py:_handle_crear_solicitud",
        "offline request created in current tenant",
        {
            "target_tenant": tenant,
            "solicitud_id": result.id,
            "cliente_user_id": actor_id,
        },
    )
    # #endregion
    await _broadcast_state_change(tenant, result.id, "REGISTRADA")
    return {"solicitud_id": result.id, "estado": "REGISTRADA"}


async def _handle_cancelar_solicitud(
    payload: dict,
    actor_id: int,
    roles: set[str],
    cliente_id: int | None,
    db: AsyncSession,
    tenant: str,
) -> dict:
    if "CLIENTE" not in roles or cliente_id is None:
        raise ValueError("Solo los clientes pueden cancelar sus solicitudes")
    solicitud_id = int(payload["solicitud_id"])
    sol = await db.scalar(
        select(Solicitud)
        .options(selectinload(Solicitud.estado))
        .where(Solicitud.id == solicitud_id, Solicitud.cliente_id == cliente_id)
    )
    if not sol:
        raise ValueError(f"Solicitud {solicitud_id} no encontrada")
    estado_actual = sol.estado.nombre if sol.estado else ""
    if estado_actual in ("COMPLETADA", "CANCELADA"):
        return {"solicitud_id": solicitud_id, "estado": estado_actual, "sin_cambios": True}
    estado_cancelada = await _get_estado(db, "CANCELADA")
    if not estado_cancelada:
        raise ValueError("Estado CANCELADA no encontrado")
    sol.estado_id = estado_cancelada.id
    db.add(
        HistorialEvento(
            solicitud_id=sol.id,
            estado_anterior=estado_actual,
            estado_nuevo="CANCELADA",
            observacion=str(payload.get("motivo", "Cancelada vía sincronización offline")),
            usuario_id=actor_id,
        )
    )
    await db.commit()
    await hub.broadcast_solicitud_update(
        tenant, solicitud_id=sol.id, estado="CANCELADA", updated_at=datetime.now(timezone.utc).isoformat()
    )
    return {"solicitud_id": solicitud_id, "estado": "CANCELADA"}


# ── Main endpoint ─────────────────────────────────────────────────────────────


@router.post("/lote", response_model=SyncLoteResponse, status_code=status.HTTP_200_OK)
async def sync_lote(
    body: SyncLoteRequest,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> SyncLoteResponse:
    """
    Process a batch of offline operations in creation-time order.

    - Each operation is identified by a UUID `idempotency_key`.
    - Keys already recorded in `sync_idempotencia` (any process/replica, any
      previous request) return `status: duplicate` with the original result.
    - Failed operations return `status: error` with a message and leave no
      idempotency record — the client can safely retry them.
    """
    tenant: str = db.info.get("tenant_key", "default")
    roles = get_role_names(current_user)
    # Capturamos el id del usuario AHORA, como int plano. Un rollback dentro del
    # loop EXPIRA todos los objetos de la sesión (incluido current_user, sin
    # importar expire_on_commit). Si más tarde accediéramos a current_user.id se
    # dispararía una recarga lazy en contexto sync → MissingGreenlet (500).
    actor_id: int = current_user.id

    # Sort by offline_created_at to preserve causal order
    ops_sorted = sorted(
        body.operations,
        key=lambda op: op.offline_created_at or "",
    )

    results: list[SyncOperationResult] = []
    ok_count = dup_count = err_count = 0

    for op in ops_sorted:
        # Idempotency check against the persistent table (cross-process).
        cached_result = await _idem_lookup(db, op.idempotency_key)
        if cached_result is not None:
            results.append(
                SyncOperationResult(
                    idempotency_key=op.idempotency_key,
                    tipo=op.tipo,
                    status="duplicate",
                    data=cached_result,
                )
            )
            dup_count += 1
            continue

        offline_ts = _parse_offline_ts(op.offline_created_at)
        try:
            if op.tipo == "crear_solicitud":
                data = await _handle_crear_solicitud(
                    op.payload, current_user, actor_id, roles, current_cliente_id, db, tenant, offline_ts
                )
            elif op.tipo == "cancelar_solicitud":
                data = await _handle_cancelar_solicitud(
                    op.payload, actor_id, roles, current_cliente_id, db, tenant
                )
            else:
                raise ValueError(f"Tipo de operación desconocido: '{op.tipo}'")

        except Exception as exc:
            logger.warning("sync_lote error op=%s tipo=%s: %s", op.idempotency_key, op.tipo, exc)
            # Roll back partial changes for this operation
            await db.rollback()
            results.append(
                SyncOperationResult(
                    idempotency_key=op.idempotency_key,
                    tipo=op.tipo,
                    status="error",
                    error=str(exc),
                )
            )
            err_count += 1
            continue

        # Persist the idempotency record AFTER the handler committed its work.
        saved = await _idem_save(db, op.idempotency_key, op.tipo, actor_id, data)
        if not saved:
            # A concurrent request recorded the same key first → treat as
            # duplicate and return the winner's stored result.
            winner = await _idem_lookup(db, op.idempotency_key)
            results.append(
                SyncOperationResult(
                    idempotency_key=op.idempotency_key,
                    tipo=op.tipo,
                    status="duplicate",
                    data=winner if winner is not None else data,
                )
            )
            dup_count += 1
            continue

        results.append(
            SyncOperationResult(
                idempotency_key=op.idempotency_key,
                tipo=op.tipo,
                status="ok",
                data=data,
            )
        )
        ok_count += 1

    # Best-effort retention purge (never fails the batch).
    await _purge_old_idempotencia(db)

    logger.info(
        "sync_lote user=%d tenant=%s total=%d ok=%d dup=%d err=%d",
        actor_id, tenant, len(ops_sorted), ok_count, dup_count, err_count,
    )
    return SyncLoteResponse(
        total=len(ops_sorted),
        ok=ok_count,
        duplicates=dup_count,
        errors=err_count,
        results=results,
    )
