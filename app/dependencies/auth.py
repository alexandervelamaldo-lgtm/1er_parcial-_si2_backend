from collections.abc import Awaitable, Callable

from jose import JWTError
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.clientes import Cliente
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.utils.auth import decode_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    email = payload.get("sub")
    if not isinstance(email, str) or not email.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    # ── Super-admin: vive en la control DB, NO en tenant DBs ─────────
    # Reconocemos el JWT solo cuando AMBOS claims coinciden: `is_super_admin=True`
    # Y `tenant="*"`. Usar `and` (no `or`) hace que tokens legacy o
    # malformados NO entren a esta rama por accidente — fallarían
    # buscando un user fantasma en la control DB.
    if payload.get("is_super_admin") is True and payload.get("tenant") == "*":
        return await _resolve_super_admin_user(email)

    token_tenant = payload.get("tenant")
    raw_roles = payload.get("roles") or []
    token_roles = {str(role).strip().upper() for role in raw_roles} if isinstance(raw_roles, list) else set()
    request_tenant = getattr(request.state, "tenant_key", None)
    if isinstance(token_tenant, str) and isinstance(request_tenant, str) and token_tenant.strip() and request_tenant.strip():
        if token_tenant.strip() != request_tenant.strip() and "CLIENTE" not in token_roles:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido para este tenant")

    result = await db.execute(select(User).options(selectinload(User.roles)).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")
    return user


async def _resolve_super_admin_user(email: str) -> User:
    """Devuelve un objeto User "virtual" para un super-admin.

    No es una fila real de la tabla `users` de ningún tenant — es un
    proxy que tiene los atributos que el resto del código consume
    (id, email, is_active, roles). Lo construimos dinámicamente a
    partir de la fila en la control DB.

    Si el super-admin fue suspendido O ya no existe en control DB,
    rechazamos el token con 401.
    """
    from app.control_plane.database import get_control_sessionmaker
    from app.control_plane.models.super_admin import SuperAdmin
    from app.models.roles import Role

    try:
        sessionmaker = get_control_sessionmaker()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Control plane no disponible — no se puede validar super-admin",
        )

    async with sessionmaker() as session:
        admin = await session.scalar(
            select(SuperAdmin).where(SuperAdmin.email == email)
        )
        if not admin:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Super-admin no existe")
        if admin.suspended:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super-admin suspendido")

    # Construimos un User SQLAlchemy NO persistido — solo el shape que
    # consume el resto del código. Le inyectamos un Role "SUPER_ADMIN"
    # transient para que get_role_names lo encuentre.
    virtual_role = Role(id=0, name="SUPER_ADMIN")
    virtual_user = User(
        id=admin.id,
        email=admin.email,
        password_hash="",  # nunca se usa para reauth — el JWT ya validó
        is_active=not admin.suspended,
    )
    # Asignamos roles directamente al atributo de instancia. La
    # colección normalmente requiere una sesión, pero como es un
    # objeto transient (no detached), Python permite la asignación.
    virtual_user.roles = [virtual_role]
    return virtual_user


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
