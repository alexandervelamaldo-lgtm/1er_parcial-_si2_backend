from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_tecnico_id, get_current_user, get_role_names, require_roles
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.schemas.tecnicos import (
    DisponibilidadTecnicoUpdate,
    TecnicoCreate,
    TecnicoResponse,
    TecnicoUpdate,
    UbicacionTecnicoUpdate,
)


router = APIRouter(prefix="/tecnicos", tags=["Técnicos"])


def validate_technician_access(current_user: User, current_tecnico_id: int | None, tecnico_id: int) -> None:
    roles = get_role_names(current_user)
    if roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        return
    if "TECNICO" in roles and current_tecnico_id == tecnico_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes administrar este técnico")


@router.get("", response_model=list[TecnicoResponse])
async def list_technicians(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Tecnico]:
    result = await db.execute(select(Tecnico).options(selectinload(Tecnico.user)))
    return list(result.scalars().all())


@router.post("", response_model=TecnicoResponse, status_code=status.HTTP_201_CREATED)
async def create_technician(
    payload: TecnicoCreate,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    tecnico = Tecnico(**payload.model_dump())
    db.add(tecnico)
    await db.commit()
    await db.refresh(tecnico)
    return tecnico


@router.get("/{tecnico_id}", response_model=TecnicoResponse)
async def get_technician(
    tecnico_id: int,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    result = await db.execute(select(Tecnico).options(selectinload(Tecnico.user)).where(Tecnico.id == tecnico_id))
    tecnico = result.scalar_one_or_none()
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    return tecnico


@router.put("/{tecnico_id}", response_model=TecnicoResponse)
async def update_technician(
    tecnico_id: int,
    payload: TecnicoUpdate,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    validate_technician_access(current_user, current_tecnico_id, tecnico_id)
    tecnico = await db.get(Tecnico, tecnico_id)
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")

    for field, value in payload.model_dump(exclude_unset=True).items():
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
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    validate_technician_access(current_user, current_tecnico_id, tecnico_id)
    tecnico = await db.get(Tecnico, tecnico_id)
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    tecnico.latitud_actual = payload.latitud_actual
    tecnico.longitud_actual = payload.longitud_actual
    await db.commit()
    await db.refresh(tecnico)
    return tecnico


@router.put("/{tecnico_id}/disponibilidad", response_model=TecnicoResponse)
async def update_technician_availability(
    tecnico_id: int,
    payload: DisponibilidadTecnicoUpdate,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    validate_technician_access(current_user, current_tecnico_id, tecnico_id)
    tecnico = await db.get(Tecnico, tecnico_id)
    if not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    tecnico.disponibilidad = payload.disponibilidad
    await db.commit()
    await db.refresh(tecnico)
    return tecnico
