"""Router de respaldos — backup manual + automático por tenant.

Cada organización (taller) respalda y restaura ÚNICAMENTE su propia base de
datos. El tenant se resuelve del request (header ``X-Tenant`` / JWT) con
``resolve_tenant_key`` y nunca se acepta como parámetro libre, así un taller
jamás opera sobre la BD de otro.

Endpoints (prefijo ``/backups``):
  GET    ""                 → lista de respaldos del tenant
  POST   ""                 → crea un respaldo manual ahora
  GET    "/schedule"        → configuración del backup automático
  PUT    "/schedule"        → actualiza la configuración automática
  GET    "/{name}/download" → descarga el .dump
  POST   "/{name}/restore"  → restaura (⚠ destructivo: reemplaza la BD)
  DELETE "/{name}"          → borra un respaldo

Restringido a roles administrativos/operativos. Las acciones mutantes quedan
auditadas por ``TenantAuditMiddleware`` (integración con la Bitácora).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.dependencies.auth import require_roles
from app.dependencies.tenant import resolve_tenant_key
from app.models.users import User
from app.schemas.gestion_operativa_web.backup import (
    BackupItem,
    BackupListResponse,
    MessageResponse,
    ScheduleConfig,
    ScheduleResponse,
)
from app.services.gestion_operativa_web import backup_service
from app.services.gestion_operativa_web.backup_service import (
    BackupError,
    BackupNotFound,
    PgToolsUnavailable,
)

router = APIRouter(prefix="/backups", tags=["Backups"])

# Mismos roles que el resto del panel de gestión por-tenant.
_ROLES = ("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR")


def _schedule_response(cfg: dict) -> ScheduleResponse:
    now = datetime.now(timezone.utc)
    return ScheduleResponse(
        enabled=bool(cfg.get("enabled")),
        frequency=cfg.get("frequency", "daily"),
        hour=int(cfg.get("hour", 2)),
        retention=int(cfg.get("retention", 7)),
        last_run=cfg.get("last_run"),
        next_run=backup_service.next_run_iso(cfg, now),
    )


@router.get("", response_model=BackupListResponse)
async def list_backups(
    request: Request,
    _: User = Depends(require_roles(*_ROLES)),
) -> BackupListResponse:
    """Lista los respaldos del tenant actual (más reciente primero)."""
    tenant = resolve_tenant_key(request)
    items = backup_service.list_backups(tenant)
    return BackupListResponse(
        items=[BackupItem(**meta) for meta in items],
        total=len(items),
        pg_available=backup_service.pg_tools_available(),
    )


@router.post("", response_model=BackupItem, status_code=status.HTTP_201_CREATED)
async def create_backup(
    request: Request,
    _: User = Depends(require_roles(*_ROLES)),
) -> BackupItem:
    """Genera un respaldo manual de la BD del tenant ahora mismo."""
    tenant = resolve_tenant_key(request)
    try:
        meta = await backup_service.create_backup(tenant, kind="manual")
    except PgToolsUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except BackupError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return BackupItem(**meta)


# ── Schedule (declarado ANTES de las rutas dinámicas /{name}) ─────────────


@router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule(
    request: Request,
    _: User = Depends(require_roles(*_ROLES)),
) -> ScheduleResponse:
    """Devuelve la configuración del backup automático del tenant."""
    tenant = resolve_tenant_key(request)
    return _schedule_response(backup_service.load_schedule(tenant))


@router.put("/schedule", response_model=ScheduleResponse)
async def update_schedule(
    payload: ScheduleConfig,
    request: Request,
    _: User = Depends(require_roles(*_ROLES)),
) -> ScheduleResponse:
    """Actualiza la programación del backup automático del tenant."""
    tenant = resolve_tenant_key(request)
    cfg = backup_service.save_schedule(
        tenant,
        enabled=payload.enabled,
        frequency=payload.frequency,
        hour=payload.hour,
        retention=payload.retention,
    )
    return _schedule_response(cfg)


# ── Rutas por archivo ────────────────────────────────────────────────────


@router.get("/{name}/download")
async def download_backup(
    name: str,
    request: Request,
    _: User = Depends(require_roles(*_ROLES)),
) -> FileResponse:
    """Descarga el archivo .dump del respaldo indicado."""
    tenant = resolve_tenant_key(request)
    try:
        path = backup_service.resolve_backup_path(tenant, name)
    except BackupNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Respaldo no encontrado.")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
    )


@router.post("/{name}/restore", response_model=MessageResponse)
async def restore_backup(
    name: str,
    request: Request,
    _: User = Depends(require_roles(*_ROLES)),
) -> MessageResponse:
    """Restaura la BD del tenant desde un respaldo.

    ⚠ Operación destructiva: ``pg_restore --clean`` reemplaza los datos
    actuales por los del respaldo. La UI exige confirmación explícita.
    """
    tenant = resolve_tenant_key(request)
    try:
        await backup_service.restore_backup(tenant, name)
    except BackupNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Respaldo no encontrado.")
    except PgToolsUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except BackupError as exc:
        # Fallo de restauración = entrada inválida/datos incompatibles → 400.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return MessageResponse(detail=f"Respaldo '{name}' restaurado correctamente.")


@router.delete("/{name}", response_model=MessageResponse)
async def delete_backup(
    name: str,
    request: Request,
    _: User = Depends(require_roles(*_ROLES)),
) -> MessageResponse:
    """Elimina un archivo de respaldo del tenant."""
    tenant = resolve_tenant_key(request)
    try:
        backup_service.delete_backup(tenant, name)
    except BackupNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Respaldo no encontrado.")
    except BackupError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return MessageResponse(detail=f"Respaldo '{name}' eliminado.")
