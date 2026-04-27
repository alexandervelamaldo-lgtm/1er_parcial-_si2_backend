from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_cliente_id, get_current_user, get_role_names, require_roles
from app.models.clientes import Cliente
from app.models.roles import Role
from app.models.solicitudes import Solicitud
from app.models.users import User
from app.models.vehiculos import Vehiculo
from app.schemas.clientes import ClienteCreate, ClienteResponse, ClienteUpdate
from app.schemas.solicitudes import SolicitudResponse
from app.schemas.vehiculos import VehiculoResponse
from app.utils.auth import hash_password


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
    result = await db.execute(
        select(Cliente).options(
            selectinload(Cliente.user).selectinload(User.roles),
            selectinload(Cliente.vehiculos),
        )
    )
    return list(result.scalars().all())


@router.post("", response_model=ClienteResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClienteCreate,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Cliente:
    role = await db.scalar(select(Role).where(Role.name == "CLIENTE"))
    if not role:
        raise HTTPException(status_code=400, detail="Rol CLIENTE no configurado")

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

    existing_client = await db.scalar(select(Cliente).where(Cliente.user_id == user.id))
    if existing_client:
        raise HTTPException(status_code=400, detail="El usuario ya tiene un perfil de cliente")
    existing_vehicle = await db.scalar(select(Vehiculo).where(Vehiculo.placa == payload.vehiculo.placa.strip().upper()))
    if existing_vehicle:
        raise HTTPException(status_code=400, detail="La placa del vehículo ya está registrada")

    if all(item.name != "CLIENTE" for item in user.roles):
        user.roles.append(role)

    cliente = Cliente(
        user_id=user.id,
        nombre=payload.nombre,
        telefono=payload.telefono,
        direccion=payload.direccion,
        latitud=payload.latitud,
        longitud=payload.longitud,
    )
    db.add(cliente)
    await db.flush()

    vehiculo = Vehiculo(
        cliente_id=cliente.id,
        marca=payload.vehiculo.marca.strip(),
        modelo=payload.vehiculo.modelo.strip(),
        anio=payload.vehiculo.anio,
        placa=payload.vehiculo.placa.strip().upper(),
        color=payload.vehiculo.color.strip(),
        tipo_combustible=payload.vehiculo.tipo_combustible.strip(),
    )
    db.add(vehiculo)
    await db.commit()
    result = await db.execute(
        select(Cliente)
        .options(
            selectinload(Cliente.user).selectinload(User.roles),
            selectinload(Cliente.vehiculos),
        )
        .where(Cliente.id == cliente.id)
    )
    created_cliente = result.scalar_one_or_none()
    if not created_cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return created_cliente


@router.get("/{client_id}", response_model=ClienteResponse)
async def get_client(
    client_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Cliente:
    validate_client_access(current_user, current_cliente_id, client_id)
    result = await db.execute(
        select(Cliente)
        .options(
            selectinload(Cliente.user).selectinload(User.roles),
            selectinload(Cliente.vehiculos),
        )
        .where(Cliente.id == client_id)
    )
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
    result = await db.execute(
        select(Cliente)
        .options(
            selectinload(Cliente.user).selectinload(User.roles),
            selectinload(Cliente.vehiculos),
        )
        .where(Cliente.id == client_id)
    )
    updated_cliente = result.scalar_one_or_none()
    if not updated_cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return updated_cliente


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
