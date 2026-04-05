from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.clientes import Cliente
from app.models.operadores import Operador
from app.models.roles import Role
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.schemas.auth import (
    CurrentUserProfileResponse,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    RegisterWorkshopRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.users import UserResponse
from app.utils.auth import create_access_token, hash_password, verify_password


router = APIRouter(prefix="/auth", tags=["Auth"])


def build_token_response(user: User) -> TokenResponse:
    roles = [role.name for role in user.roles]
    token = create_access_token(user.email, extra={"roles": roles, "user_id": user.id})
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    existing_user = await db.scalar(select(User).where(User.email == payload.email))
    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    if payload.rol.upper() != "CLIENTE":
        raise HTTPException(status_code=403, detail="El registro público solo está habilitado para clientes")

    role = await db.scalar(select(Role).where(Role.name == payload.rol.upper()))
    if not role:
        raise HTTPException(status_code=400, detail="Rol no válido")

    # Se crea el usuario base y luego se materializa el perfil del dominio.
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    user.roles.append(role)
    db.add(user)
    await db.flush()

    if role.name == "CLIENTE":
        db.add(
            Cliente(
                user_id=user.id,
                nombre=payload.nombre,
                telefono=payload.telefono,
                direccion=payload.direccion or "Sin dirección registrada",
            )
        )
    elif role.name == "TECNICO":
        db.add(
            Tecnico(
                user_id=user.id,
                nombre=payload.nombre,
                telefono=payload.telefono,
                especialidad="Asistencia general",
                disponibilidad=True,
            )
        )
    elif role.name == "OPERADOR":
        db.add(Operador(user_id=user.id, nombre=payload.nombre, turno="Mañana"))

    await db.commit()
    await db.refresh(user, attribute_names=["roles"])
    return build_token_response(user)


@router.post("/register-workshop", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_workshop(payload: RegisterWorkshopRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    existing_user = await db.scalar(select(User).where(User.email == payload.email))
    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    role = await db.scalar(select(Role).where(Role.name == "TALLER"))
    if not role:
        raise HTTPException(status_code=400, detail="Rol TALLER no configurado")

    user = User(email=payload.email, password_hash=hash_password(payload.password))
    user.roles.append(role)
    db.add(user)
    await db.flush()

    taller = Taller(
        user_id=user.id,
        nombre=payload.nombre_taller,
        direccion=payload.direccion,
        latitud=payload.latitud,
        longitud=payload.longitud,
        telefono=payload.telefono,
        capacidad=payload.capacidad,
        servicios="|".join(payload.servicios),
        disponible=True,
        acepta_automaticamente=False,
    )
    db.add(taller)
    await db.commit()
    await db.refresh(user, attribute_names=["roles"])
    return build_token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await db.scalar(select(User).options(selectinload(User.roles)).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")
    return build_token_response(user)


@router.post("/reset-password", response_model=TokenResponse)
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await db.scalar(select(User).options(selectinload(User.roles)).where(User.email == payload.email))
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return build_token_response(user)


@router.post("/refresh-token", response_model=TokenResponse)
async def refresh_token(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await db.scalar(select(User).options(selectinload(User.roles)).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No se pudo refrescar el token")
    return build_token_response(user)


@router.get("/me", response_model=CurrentUserProfileResponse)
async def get_my_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentUserProfileResponse:
    cliente_id = await db.scalar(select(Cliente.id).where(Cliente.user_id == current_user.id))
    tecnico_id = await db.scalar(select(Tecnico.id).where(Tecnico.user_id == current_user.id))
    operador_id = await db.scalar(select(Operador.id).where(Operador.user_id == current_user.id))
    taller_id = await db.scalar(select(Taller.id).where(Taller.user_id == current_user.id))
    return CurrentUserProfileResponse(
        user=UserResponse.model_validate(current_user),
        cliente_id=cliente_id,
        tecnico_id=tecnico_id,
        operador_id=operador_id,
        taller_id=taller_id,
    )


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    payload: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual no es válida")
    current_user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return build_token_response(current_user)
