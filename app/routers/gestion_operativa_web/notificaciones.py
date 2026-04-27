from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user, get_role_names
from app.models.device_tokens import UserDeviceToken
from app.models.notificaciones import Notificacion
from app.models.users import User
from app.schemas.notificaciones import DeviceTokenRegisterRequest, NotificacionResponse


router = APIRouter(prefix="/notificaciones", tags=["Notificaciones"])


@router.get("", response_model=list[NotificacionResponse])
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Notificacion]:
    query = select(Notificacion).order_by(desc(Notificacion.fecha_creacion))
    if not get_role_names(current_user).intersection({"ADMINISTRADOR", "OPERADOR"}):
        query = query.where(Notificacion.usuario_id == current_user.id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.put("/{notificacion_id}/leida", response_model=NotificacionResponse)
async def mark_notification_as_read(
    notificacion_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Notificacion:
    notificacion = await db.get(Notificacion, notificacion_id)
    if not notificacion:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    if not get_role_names(current_user).intersection({"ADMINISTRADOR", "OPERADOR"}) and notificacion.usuario_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes modificar esta notificación")
    notificacion.leida = True
    await db.commit()
    await db.refresh(notificacion)
    return notificacion


@router.post("/device-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_device_token(
    payload: DeviceTokenRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    existing = await db.scalar(
        select(UserDeviceToken).where(
            UserDeviceToken.user_id == current_user.id,
            UserDeviceToken.token == payload.token,
        )
    )
    if existing:
        existing.plataforma = payload.plataforma
    else:
        db.add(
            UserDeviceToken(
                user_id=current_user.id,
                token=payload.token,
                plataforma=payload.plataforma,
            )
        )
    await db.commit()
