from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_taller_id, get_current_tecnico_id, get_current_user, get_role_names, require_roles
from app.models.roles import Role
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.schemas.tecnicos import (
    DisponibilidadTecnicoUpdate,
    TecnicoCreate,
    TecnicoResponse,
    TecnicoUpdate,
    UbicacionTecnicoUpdate,
)
from app.utils.auth import hash_password


router = APIRouter(prefix="/tecnicos", tags=["Técnicos"])


def validate_technician_access(
    current_user: User,
    current_tecnico_id: int | None,
    current_taller_id: int | None,
    tecnico: Tecnico,
) -> None:
    roles = get_role_names(current_user)
    if roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        return
    if "TECNICO" in roles and current_tecnico_id == tecnico.id:
        return
    if "TALLER" in roles and current_taller_id is not None and tecnico.taller_id == current_taller_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes administrar este técnico")


@router.get("", response_model=list[TecnicoResponse])
async def list_technicians(
    current_user: User = Depends(get_current_user),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> list[Tecnico]:
    query = select(Tecnico).options(selectinload(Tecnico.user))
    if "TALLER" in get_role_names(current_user) and current_taller_id is not None:
        query = query.where(Tecnico.taller_id == current_taller_id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("", response_model=TecnicoResponse, status_code=status.HTTP_201_CREATED)
async def create_technician(
    payload: TecnicoCreate,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    role = await db.scalar(select(Role).where(Role.name == "TECNICO"))
    if not role:
        raise HTTPException(status_code=400, detail="Rol TECNICO no configurado")

    user: User | None = None
    if payload.user_id is not None:
        user = await db.scalar(select(User).options(selectinload(User.roles)).where(User.id == payload.user_id))
        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
    else:
        existing_user = await db.scalar(select(User).where(User.email == payload.email))
        if existing_user:
            raise HTTPException(status_code=400, detail="El correo ya está registrado")
        user = User(email=str(payload.email), password_hash=hash_password(payload.password or ""))
        user.roles.append(role)
        db.add(user)
        await db.flush()

    existing_technician = await db.scalar(select(Tecnico).where(Tecnico.user_id == user.id))
    if existing_technician:
        raise HTTPException(status_code=400, detail="El usuario ya tiene un perfil técnico")

    if all(item.name != "TECNICO" for item in user.roles):
        user.roles.append(role)

    tecnico = Tecnico(
        user_id=user.id,
        nombre=payload.nombre,
        telefono=payload.telefono,
        especialidad=payload.especialidad,
        taller_id=payload.taller_id,
        latitud_actual=payload.latitud_actual,
        longitud_actual=payload.longitud_actual,
        disponibilidad=payload.disponibilidad,
    )
    db.add(tecnico)
    await db.commit()
    result = await db.execute(
        select(Tecnico)
        .options(selectinload(Tecnico.user).selectinload(User.roles))
        .where(Tecnico.id == tecnico.id)
    )
    created_tecnico = result.scalar_one_or_none()
    if not created_tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    return created_tecnico


@router.get("/{tecnico_id}", response_model=TecnicoResponse)
async def get_technician(
    tecnico_id: int,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    result = await db.execute(select(Tecnico).options(selectinload(Tecnico.user)).where(Tecnico.id == tecnico_id))
    tecnico = result.scalar_one_or_none()
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    validate_technician_access(current_user, current_tecnico_id, current_taller_id, tecnico)
    return tecnico


@router.put("/{tecnico_id}", response_model=TecnicoResponse)
async def update_technician(
    tecnico_id: int,
    payload: TecnicoUpdate,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    tecnico = await db.get(Tecnico, tecnico_id)
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    validate_technician_access(current_user, current_tecnico_id, current_taller_id, tecnico)

    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "taller_id" and "TALLER" in get_role_names(current_user) and value != current_taller_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes mover el técnico a otro taller")
        setattr(tecnico, field, value)

    await db.commit()
    await db.refresh(tecnico)
    return tecnico


@router.delete("/{tecnico_id}", response_model=dict[str, str])
async def delete_technician(
    tecnico_id: int,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    tecnico = await db.get(Tecnico, tecnico_id)
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    await db.delete(tecnico)
    await db.commit()
    return {"message": "Técnico eliminado correctamente"}


@router.put("/{tecnico_id}/ubicacion", response_model=TecnicoResponse)
async def update_technician_location(
    tecnico_id: int,
    payload: UbicacionTecnicoUpdate,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    tecnico = await db.get(Tecnico, tecnico_id)
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    validate_technician_access(current_user, current_tecnico_id, current_taller_id, tecnico)
    tecnico.latitud_actual = payload.latitud_actual
    tecnico.longitud_actual = payload.longitud_actual
    tecnico.ubicacion_actualizada_en = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(tecnico)
    return tecnico


@router.put("/{tecnico_id}/disponibilidad", response_model=TecnicoResponse)
async def update_technician_availability(
    tecnico_id: int,
    payload: DisponibilidadTecnicoUpdate,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    tecnico = await db.get(Tecnico, tecnico_id)
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    validate_technician_access(current_user, current_tecnico_id, current_taller_id, tecnico)
    tecnico.disponibilidad = payload.disponibilidad
    await db.commit()
    await db.refresh(tecnico)
    return tecnico
