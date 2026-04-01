from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_cliente_id, get_current_user, get_role_names, require_roles
from app.models.clientes import Cliente
from app.models.solicitudes import Solicitud
from app.models.users import User
from app.models.vehiculos import Vehiculo
from app.schemas.clientes import ClienteCreate, ClienteResponse, ClienteUpdate
from app.schemas.solicitudes import SolicitudResponse
from app.schemas.vehiculos import VehiculoResponse


router = APIRouter(prefix="/clientes", tags=["Clientes"])


def validate_client_access(current_user: User, current_cliente_id: int | None, client_id: int) -> None:
    roles = get_role_names(current_user)
    if roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        return
    if "CLIENTE" in roles and current_cliente_id == client_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes acceder a este cliente")


@router.get("", response_model=list[ClienteResponse])
async def list_clients(
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> list[Cliente]:
    result = await db.execute(select(Cliente).options(selectinload(Cliente.user)))
    return list(result.scalars().all())


@router.post("", response_model=ClienteResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClienteCreate,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Cliente:
    cliente = Cliente(**payload.model_dump())
    db.add(cliente)
    await db.commit()
    await db.refresh(cliente)
    return cliente


@router.get("/{client_id}", response_model=ClienteResponse)
async def get_client(
    client_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Cliente:
    validate_client_access(current_user, current_cliente_id, client_id)
    result = await db.execute(select(Cliente).options(selectinload(Cliente.user)).where(Cliente.id == client_id))
    cliente = result.scalar_one_or_none()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return cliente


@router.put("/{client_id}", response_model=ClienteResponse)
async def update_client(
    client_id: int,
    payload: ClienteUpdate,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Cliente:
    validate_client_access(current_user, current_cliente_id, client_id)
    cliente = await db.get(Cliente, client_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(cliente, field, value)

    await db.commit()
    await db.refresh(cliente)
    return cliente


@router.delete("/{client_id}", response_model=dict[str, str])
async def delete_client(
    client_id: int,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    cliente = await db.get(Cliente, client_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    await db.delete(cliente)
    await db.commit()
    return {"message": "Cliente eliminado correctamente"}


@router.get("/{client_id}/vehiculos", response_model=list[VehiculoResponse])
async def get_client_vehicles(
    client_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> list[Vehiculo]:
    validate_client_access(current_user, current_cliente_id, client_id)
    result = await db.execute(select(Vehiculo).where(Vehiculo.cliente_id == client_id))
    return list(result.scalars().all())


@router.get("/{client_id}/solicitudes", response_model=list[SolicitudResponse])
async def get_client_requests(
    client_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> list[Solicitud]:
    validate_client_access(current_user, current_cliente_id, client_id)
    result = await db.execute(
        select(Solicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .where(Solicitud.cliente_id == client_id)
    )
    return list(result.scalars().all())
