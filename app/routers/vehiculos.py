from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_cliente_id, get_current_user, get_role_names
from app.models.vehiculos import Vehiculo
from app.models.users import User
from app.schemas.vehiculos import VehiculoCreate, VehiculoResponse, VehiculoUpdate


router = APIRouter(prefix="/vehiculos", tags=["Vehículos"])


def validate_vehicle_access(current_user: User, current_cliente_id: int | None, vehicle: Vehiculo) -> None:
    roles = get_role_names(current_user)
    if roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        return
    if "CLIENTE" in roles and current_cliente_id == vehicle.cliente_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes acceder a este vehículo")


@router.get("", response_model=list[VehiculoResponse])
async def list_vehicles(
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> list[Vehiculo]:
    query = select(Vehiculo)
    if "CLIENTE" in get_role_names(current_user) and current_cliente_id is not None:
        query = query.where(Vehiculo.cliente_id == current_cliente_id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("", response_model=VehiculoResponse, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    payload: VehiculoCreate,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Vehiculo:
    if "CLIENTE" in get_role_names(current_user) and payload.cliente_id != current_cliente_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes crear vehículos para otro cliente")
    vehiculo = Vehiculo(**payload.model_dump())
    db.add(vehiculo)
    await db.commit()
    await db.refresh(vehiculo)
    return vehiculo


@router.get("/{vehiculo_id}", response_model=VehiculoResponse)
async def get_vehicle(
    vehiculo_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Vehiculo:
    vehiculo = await db.get(Vehiculo, vehiculo_id)
    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    validate_vehicle_access(current_user, current_cliente_id, vehiculo)
    return vehiculo


@router.put("/{vehiculo_id}", response_model=VehiculoResponse)
async def update_vehicle(
    vehiculo_id: int,
    payload: VehiculoUpdate,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Vehiculo:
    vehiculo = await db.get(Vehiculo, vehiculo_id)
    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    validate_vehicle_access(current_user, current_cliente_id, vehiculo)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(vehiculo, field, value)
    await db.commit()
    await db.refresh(vehiculo)
    return vehiculo


@router.delete("/{vehiculo_id}", response_model=dict[str, str])
async def delete_vehicle(
    vehiculo_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    vehiculo = await db.get(Vehiculo, vehiculo_id)
    if not vehiculo:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    validate_vehicle_access(current_user, current_cliente_id, vehiculo)
    await db.delete(vehiculo)
    await db.commit()
    return {"message": "Vehículo eliminado correctamente"}
