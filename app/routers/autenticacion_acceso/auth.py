import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.control_plane.database import get_control_sessionmaker
from app.control_plane.models.super_admin import SuperAdmin
from app.database import get_db, get_tenant_sessionmaker
from app.dependencies.auth import get_current_user

logger = logging.getLogger(__name__)


# Marcador especial de tenant para super-admins — significa "este token
# no pertenece a ningún tenant; puede operar sobre cualquiera". El
# middleware/dependencies lo tratan distinto: no resuelve get_db
# automáticamente, sino que el endpoint decide a qué tenant ir.
SUPER_ADMIN_TENANT_MARKER = "*"
from app.models.clientes import Cliente
from app.models.operadores import Operador
from app.models.roles import Role
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.schemas.autenticacion_acceso.auth import (
    CurrentUserProfileResponse,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    RegisterWorkshopRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.autenticacion_acceso.roles import RoleResponse
from app.schemas.autenticacion_acceso.users import UserResponse
from app.utils.auth import create_access_token, hash_password, verify_password


router = APIRouter(prefix="/auth", tags=["Auth"])


def build_token_response(user: User, tenant_key: str) -> TokenResponse:
    roles = [role.name for role in user.roles]
    token = create_access_token(user.email, extra={"roles": roles, "user_id": user.id, "tenant": tenant_key})
    # Incluimos `tenant_key` en el response — el cliente lo guarda para
    # mandarlo en X-Tenant en requests subsiguientes. Así el login no
    # necesita preguntar la organización: el backend la detecta sola.
    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
        tenant_key=tenant_key,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
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
    return build_token_response(user, request.state.tenant_key)


@router.post("/register-workshop", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_workshop(payload: RegisterWorkshopRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
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
    return build_token_response(user, request.state.tenant_key)


async def _find_super_admin(email: str, password: str) -> SuperAdmin | None:
    """Busca un super-admin activo en la control DB.

    Devuelve un snapshot DETACHED del super-admin si el password matchea
    y NO está suspendido. None en cualquier otro caso (no existe,
    password incorrecto, suspendido). NUNCA loggea el password.

    Devolvemos un objeto detached (creado a partir de los valores de la
    fila) en lugar del ORM row pegado a la sesión — eso evita
    DetachedInstanceError cuando el caller accede a atributos después
    de que la sesión fue cerrada.
    """
    try:
        sessionmaker = get_control_sessionmaker()
    except Exception as exc:
        logger.warning("auth — control DB inaccesible (%s); skip super-admin lookup.", type(exc).__name__)
        return None
    try:
        async with sessionmaker() as session:
            row = await session.scalar(
                select(SuperAdmin).where(SuperAdmin.email == email)
            )
            if not row:
                return None
            if row.suspended:
                logger.info("auth — intento de login de super-admin SUSPENDIDO (%s).", email)
                return None
            if not verify_password(password, row.password_hash):
                return None
            # Touch last_login_at en una transacción independiente. No
            # propagamos esa referencia al caller.
            row.last_login_at = datetime.now(timezone.utc)
            await session.commit()
            # Snapshot DETACHED para devolver — el caller puede leer
            # estos atributos aunque la sesión esté cerrada.
            snapshot = SuperAdmin(
                id=row.id,
                email=row.email,
                password_hash=row.password_hash,
                suspended=row.suspended,
                display_name=row.display_name,
                created_at=row.created_at,
                last_login_at=row.last_login_at,
            )
            return snapshot
    except Exception as exc:
        logger.warning("auth — error consultando control DB: %s", type(exc).__name__)
        return None


def _build_super_admin_token(admin: SuperAdmin) -> TokenResponse:
    """Construye el TokenResponse de un super-admin.

    El JWT lleva:
      - sub: email
      - is_super_admin: True
      - tenant: "*"  (marcador especial)
      - roles: ["SUPER_ADMIN"]
    Expira más rápido que un token normal (15 min vs 30) — sus permisos
    son globales, la ventana de abuso debe ser corta.
    """
    settings = get_settings()
    expires_minutes = int(settings.super_admin_token_expire_minutes)
    token = create_access_token(
        admin.email,
        expires_minutes=expires_minutes,
        extra={
            "roles": ["SUPER_ADMIN"],
            "user_id": admin.id,
            "tenant": SUPER_ADMIN_TENANT_MARKER,
            "is_super_admin": True,
        },
    )
    # Construimos un UserResponse "sintético" — el super-admin no tiene
    # una fila User en tenants, así que armamos un payload compatible.
    # Usamos model_construct para skip validation; el role real va como
    # RoleResponse para que la serialización JSON no emita warnings.
    fake_role = RoleResponse.model_construct(id=0, name="SUPER_ADMIN")
    fake_user = UserResponse.model_construct(
        id=admin.id,
        email=admin.email,
        is_active=not admin.suspended,
        roles=[fake_role],
        created_at=admin.created_at,
        updated_at=None,
    )
    return TokenResponse(
        access_token=token,
        user=fake_user,
        tenant_key=SUPER_ADMIN_TENANT_MARKER,
    )


async def _find_user_across_tenants(
    email: str, password: str,
) -> tuple[User, str] | None:
    """Busca el usuario por email+password en TODOS los tenants configurados.

    Devuelve (user, tenant_key) del primer match. Si nadie matchea,
    devuelve None. El propósito es que el login NO requiera al cliente
    pre-seleccionar la organización — el backend la deduce.

    Seguridad: si dos tenants tienen el mismo email con el mismo
    password, ese es un escenario muy improbable y mal manejo de
    multi-tenancy a nivel humano; nos quedamos con el primero (orden
    determinístico por el dict de settings.tenant_databases).
    Tiempo: en el peor caso recorremos N tenants — N suele ser ≤ 10.
    """
    settings = get_settings()
    tenants = list((settings.tenant_databases or {}).keys())
    # Garantizamos que el tenant por defecto se evalúe primero — es el
    # más probable de match en una instalación típica.
    default_key = settings.default_tenant or "default"
    if default_key in tenants:
        tenants.remove(default_key)
    tenants.insert(0, default_key)

    for tenant in tenants:
        try:
            sessionmaker = get_tenant_sessionmaker(tenant)
        except Exception:
            continue
        async with sessionmaker() as session:
            session.info["tenant_key"] = tenant
            try:
                user = await session.scalar(
                    select(User)
                    .options(selectinload(User.roles))
                    .where(User.email == email)
                )
            except Exception as exc:
                # Tenant con problemas (DB caída, schema desactualizado).
                # Loggeamos sin filtrar credenciales y seguimos.
                logger.warning("auth.login: tenant=%s no respondió (%s)", tenant, type(exc).__name__)
                continue
            if user and verify_password(password, user.password_hash):
                # Recargamos los roles "selectin" antes de cerrar la
                # sesión para evitar MissingGreenlet al serializar.
                _ = [r.name for r in user.roles]
                return user, tenant
    return None


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
) -> TokenResponse:
    """Login con auto-detección de tenant.

    Si el cliente NO envía X-Tenant (o envía el default), buscamos el
    usuario en todos los tenants. Cuando el cliente sí envía un X-Tenant
    explícito distinto del default, respetamos ese para casos donde el
    operador quiere forzar una organización específica.
    """
    settings = get_settings()
    default_key = settings.default_tenant or "default"
    explicit_tenant = request.headers.get("x-tenant", "").strip()
    client_platform = request.headers.get("x-client-platform", "").lower()

    # 1) Antes que nada, ¿es un super-admin? — viven en la control DB,
    #    aparte de todos los tenants. Si matchea, tiene precedencia
    #    sobre cualquier user con el mismo email dentro de un tenant.
    super_admin = await _find_super_admin(payload.email, payload.password)
    if super_admin:
        # Defense-in-depth: super-admins son panel web-only.
        if client_platform == "mobile":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Los super-admins deben usar el portal web.",
            )
        logger.info("auth — super-admin login OK (id=%d, email=%s)", super_admin.id, super_admin.email)
        return _build_super_admin_token(super_admin)

    # 2) No es super-admin: flujo normal de usuarios por tenant.
    # Si X-Tenant es vacío o el default → auto-detect.
    # Si es explícito y distinto del default → solo probar ese tenant.
    if not explicit_tenant or explicit_tenant == default_key:
        match = await _find_user_across_tenants(payload.email, payload.password)
    else:
        # Override manual: probar solo el tenant indicado.
        try:
            sessionmaker = get_tenant_sessionmaker(explicit_tenant)
        except Exception:
            raise HTTPException(status_code=400, detail="Organización no válida")
        async with sessionmaker() as session:
            user = await session.scalar(
                select(User).options(selectinload(User.roles))
                .where(User.email == payload.email)
            )
            if user and verify_password(payload.password, user.password_hash):
                _ = [r.name for r in user.roles]
                match = (user, explicit_tenant)
            else:
                match = None

    if not match:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas")

    user, tenant_key = match
    # `client_platform` ya fue leído arriba.
    if client_platform == "web" and any(role.name == "CLIENTE" for role in user.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Los clientes no pueden ingresar desde la web. Usa la aplicación móvil.",
        )
    return build_token_response(user, tenant_key)


@router.post("/reset-password", response_model=TokenResponse)
async def reset_password(payload: ResetPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Endpoint deshabilitado. Usa /auth/change-password (autenticado) o implementa un flujo seguro de recuperación.",
    )


@router.post("/refresh-token", response_model=TokenResponse)
async def refresh_token(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await db.scalar(select(User).options(selectinload(User.roles)).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No se pudo refrescar el token")
    return build_token_response(user, request.state.tenant_key)


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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La contraseña actual no es válida")
    current_user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return build_token_response(current_user, request.state.tenant_key)
