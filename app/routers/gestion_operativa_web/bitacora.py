"""Bitácora router — consulta de auditoría de acciones de usuario.

Endpoint:
  GET /bitacora  → lista paginada y filtrable de acciones mutantes
                   registradas por `TenantAuditMiddleware`.

Aislamiento multi-tenant: la sesión inyectada por `get_db` ya está acotada
al schema/DB del tenant del request, por lo que la consulta solo ve las
filas de ESE tenant. Restringido a roles administrativos/operativos.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import require_roles
from app.models.bitacora import Bitacora
from app.models.users import User
from app.schemas.gestion_operativa_web.bitacora import (
    BitacoraItemResponse,
    BitacoraListResponse,
)

router = APIRouter(prefix="/bitacora", tags=["Bitácora"])


@router.get("", response_model=BitacoraListResponse)
async def list_bitacora(
    since: datetime | None = Query(None, description="Desde (ISO 8601)"),
    until: datetime | None = Query(None, description="Hasta (ISO 8601)"),
    user_id: int | None = Query(None, description="Filtrar por id de usuario"),
    entidad: str | None = Query(None, description="Filtrar por entidad (solicitud, taller, pago…)"),
    q: str | None = Query(None, description="Búsqueda libre en acción o ruta"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_roles("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> BitacoraListResponse:
    """Lista la bitácora del tenant actual, más reciente primero."""
    if since and until and since > until:
        raise HTTPException(status_code=400, detail="'since' no puede ser mayor que 'until'.")

    # Filtros comunes a la query de items y a la de conteo.
    filters = []
    if since:
        filters.append(Bitacora.created_at >= since)
    if until:
        filters.append(Bitacora.created_at <= until)
    if user_id is not None:
        filters.append(Bitacora.user_id == user_id)
    if entidad:
        filters.append(Bitacora.entidad == entidad.strip().lower())
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(Bitacora.accion.ilike(like), Bitacora.ruta.ilike(like)))

    # Total (para paginación en la UI) — count separado, mismos filtros.
    count_stmt = select(func.count()).select_from(Bitacora)
    for f in filters:
        count_stmt = count_stmt.where(f)
    total = int((await db.execute(count_stmt)).scalar() or 0)

    # Items + email del usuario por OUTER JOIN (user_id puede ser NULL o
    # apuntar a un usuario ya borrado — outer join evita perder la fila).
    stmt = (
        select(Bitacora, User.email)
        .outerjoin(User, User.id == Bitacora.user_id)
    )
    for f in filters:
        stmt = stmt.where(f)
    stmt = stmt.order_by(Bitacora.created_at.desc(), Bitacora.id.desc()).limit(limit).offset(offset)

    rows = (await db.execute(stmt)).all()
    items = [
        BitacoraItemResponse(
            id=row[0].id,
            created_at=row[0].created_at,
            user_id=row[0].user_id,
            user_email=row[1],
            accion=row[0].accion,
            metodo=row[0].metodo,
            ruta=row[0].ruta,
            status_code=row[0].status_code,
            entidad=row[0].entidad,
            entidad_id=row[0].entidad_id,
            ip=row[0].ip,
        )
        for row in rows
    ]

    return BitacoraListResponse(items=items, total=total, limit=limit, offset=offset)
