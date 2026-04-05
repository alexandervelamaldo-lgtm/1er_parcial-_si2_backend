from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.clientes import Cliente
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.utils.auth import get_subject_from_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    email = get_subject_from_token(token)
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    result = await db.execute(select(User).options(selectinload(User.roles)).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")
    return user


def get_role_names(user: User) -> set[str]:
    return {role.name for role in user.roles}


def require_roles(*allowed_roles: str) -> Callable[..., Awaitable[User]]:
    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not get_role_names(current_user).intersection(set(allowed_roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para realizar esta acción",
            )
        return current_user

    return dependency


async def get_current_cliente_id(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> int | None:
    if "CLIENTE" not in get_role_names(current_user):
        return None
    cliente = await db.scalar(select(Cliente.id).where(Cliente.user_id == current_user.id))
    return cliente


async def get_current_tecnico_id(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> int | None:
    if "TECNICO" not in get_role_names(current_user):
        return None
    tecnico = await db.scalar(select(Tecnico.id).where(Tecnico.user_id == current_user.id))
    return tecnico


async def get_current_taller_id(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> int | None:
    if "TALLER" not in get_role_names(current_user):
        return None
    taller = await db.scalar(select(Taller.id).where(Taller.user_id == current_user.id))
    return taller
