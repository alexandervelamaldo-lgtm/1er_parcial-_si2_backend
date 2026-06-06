"""
Provision a brand-new tenant.

This script is the single source of truth for what it means to "create a
tenant" in the system. Both the CLI (``python -m app.scripts.create_tenant
<key> <label> <admin_email> <admin_password>``) and the admin REST endpoint
(``POST /admin/tenants``) call into [provision_tenant] so the two surfaces
cannot drift apart.

Steps (idempotent — re-running with the same key is safe):

  1. CREATE DATABASE ``emergency_tenant_<key>`` on the configured Postgres
     instance (skipped if it already exists).
  2. Run ``alembic upgrade head`` against the new database to create the schema.
  3. Seed the catalog tables (roles, estados_solicitud, tipos_incidente).
  4. Create the initial admin user (``ADMIN_TENANT`` role).
  5. Register the tenant in [tenant_registry] so it shows up in the public
     ``/tenants/public`` list immediately.

The DB URL is derived from the same host/credentials used by the default
tenant — we just swap the database name. Override
``TENANT_DB_TEMPLATE`` env var if your topology needs different rules.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import get_settings
from app.utils.auth import hash_password


_KEY_RE = re.compile(r"^[a-z0-9_]+$")


def _normalize_db_url_with_database(base_url: str, database: str) -> str:
    """Replaces the database name in a SQLAlchemy URL while preserving creds and host."""
    parsed = urlparse(base_url)
    # ``parsed.path`` is e.g. "/emergency_db" — we just swap the last segment.
    return urlunparse(parsed._replace(path=f"/{database}"))


def _admin_url(base_url: str) -> str:
    """Postgres URL pointing at the maintenance database so we can CREATE DATABASE."""
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path="/postgres"))


async def _create_database_if_missing(base_url: str, db_name: str) -> None:
    """Best-effort ``CREATE DATABASE``. Idempotent: silently no-ops if the DB exists.

    Note: ``CREATE DATABASE`` cannot run inside a transaction, so we open
    the connection in AUTOCOMMIT mode.
    """
    engine = create_async_engine(_admin_url(base_url), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            )
            if exists:
                return
            # SQL injection safety: db_name is validated by _KEY_RE in the caller.
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await engine.dispose()


async def _run_alembic_migrations(db_url: str) -> None:
    """Run ``alembic upgrade head`` against the freshly-created database.

    We invoke the Alembic API directly (instead of shelling out) so this
    works from inside the admin endpoint without spawning subprocesses.

    BUG FIX (importante):
    El `alembic/env.py` da PRECEDENCIA a `os.environ['DATABASE_URL']`
    sobre el `sqlalchemy.url` del Config. Si solo seteamos el config
    option, alembic ignora nuestra URL nueva y migra contra la DB
    principal (la default), dejando la DB del tenant nuevo VACÍA. El
    seed posterior explota con "no existe la relación «roles»".
    Fix: setear también la env var antes de invocar, restaurar después.
    """
    from alembic import command
    from alembic.config import Config

    # The alembic config lives at backend/alembic.ini relative to the project.
    backend_root = Path(__file__).resolve().parents[2]
    alembic_ini = backend_root / "alembic.ini"
    if not alembic_ini.is_file():
        raise FileNotFoundError(f"Alembic config not found at {alembic_ini}")

    cfg = Config(str(alembic_ini))
    # Alembic uses sync drivers — strip the +asyncpg suffix when running migrations.
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    cfg.set_main_option("sqlalchemy.url", sync_url)

    def _upgrade_with_env_override() -> None:
        # Override la env var DATABASE_URL — env.py la consulta antes
        # que cualquier config option. Restauramos el valor original
        # al terminar para no afectar otros lookups concurrentes.
        original = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = sync_url
        try:
            command.upgrade(cfg, "head")
        finally:
            if original is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = original

    # Alembic is blocking — run it in a thread so we don't freeze the event loop.
    await asyncio.to_thread(_upgrade_with_env_override)


async def _seed_catalogs(db_url: str) -> None:
    """Inserts the catalog rows (roles, estados, tipos_incidente) the app
    needs to function. ON CONFLICT DO NOTHING makes this idempotent."""
    engine = create_async_engine(db_url, future=True)
    try:
        async with AsyncSession(engine) as session:
            await session.execute(text("""
                INSERT INTO roles (name) VALUES
                    ('CLIENTE'), ('TALLER'), ('TECNICO'),
                    ('OPERADOR'), ('ADMIN_TENANT'), ('SUPER_ADMIN')
                ON CONFLICT (name) DO NOTHING
            """))
            await session.execute(text("""
                INSERT INTO estados_solicitud (nombre) VALUES
                    ('REGISTRADA'), ('ASIGNADA'), ('EN_CAMINO'),
                    ('EN_ATENCION'), ('COMPLETADA'), ('CANCELADA'),
                    ('PENDIENTE')
                ON CONFLICT (nombre) DO NOTHING
            """))
            await session.execute(text("""
                INSERT INTO tipos_incidente (nombre, descripcion) VALUES
                    ('Choque',          'Colisión vehicular'),
                    ('Falla mecánica',  'Avería mecánica en ruta'),
                    ('Batería',         'Vehículo no arranca / batería descargada'),
                    ('Llanta ponchada', 'Neumático pinchado'),
                    ('Combustible',     'Tanque vacío')
                ON CONFLICT (nombre) DO NOTHING
            """))
            await session.commit()
    finally:
        await engine.dispose()


async def _create_admin_user(db_url: str, email: str, password: str) -> None:
    """Inserts the ADMIN_TENANT user. Idempotent — re-running just updates the password."""
    from app.models.roles import Role
    from app.models.users import User

    engine = create_async_engine(db_url, future=True)
    try:
        async with AsyncSession(engine) as session:
            role = await session.scalar(select(Role).where(Role.name == "ADMIN_TENANT"))
            if not role:
                raise RuntimeError("Role ADMIN_TENANT no fue sembrado — revisa _seed_catalogs")

            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    password_hash=hash_password(password),
                    is_active=True,
                )
                user.roles.append(role)
                session.add(user)
            else:
                user.password_hash = hash_password(password)
                if role not in user.roles:
                    user.roles.append(role)
            await session.commit()
    finally:
        await engine.dispose()


# ── Public API (used by the REST endpoint and the CLI) ──────────────────


async def provision_tenant(*, key: str, label: str, admin_email: str, admin_password: str) -> str:
    """
    End-to-end provisioning of a new tenant. Returns the connection URL that
    was registered in [tenant_registry].

    Funciona en dos modos según `settings.tenant_strategy`:

      - "database" (legacy): crea una DB Postgres física separada, corre
        alembic upgrade, siembra catálogos, crea admin.
      - "schema":  todos los tenants comparten UNA sola DB. Creamos un
        schema `tenant_<key>`, replicamos todas las tablas adentro con
        Base.metadata.create_all, sembramos catálogos y creamos admin
        — siempre via `execution_options(schema_translate_map=...)`.
    """
    key = key.strip().lower()
    if not _KEY_RE.match(key):
        raise ValueError("La clave del tenant solo admite letras minúsculas, dígitos y guión bajo")

    from app.tenant_strategy import using_schema_strategy

    if using_schema_strategy():
        return await _provision_tenant_schema_mode(key=key, label=label, admin_email=admin_email, admin_password=admin_password)

    # ── Modo database (legacy) ─────────────────────────────────────
    settings = get_settings()
    base_url = settings.database_url
    db_name = f"emergency_tenant_{key}"
    new_db_url = _normalize_db_url_with_database(base_url, db_name)

    print(f"-> [1/4] Creando base de datos '{db_name}'...")
    await _create_database_if_missing(base_url, db_name)

    print("-> [2/4] Migrando esquema con Alembic...")
    await _run_alembic_migrations(new_db_url)

    print("-> [3/4] Sembrando catálogos (roles, estados, tipos)...")
    await _seed_catalogs(new_db_url)

    print(f"-> [4/4] Creando usuario admin '{admin_email}'...")
    await _create_admin_user(new_db_url, admin_email, admin_password)

    # Make the tenant visible to the rest of the running app immediately.
    from app.services.tenant_registry import tenant_registry
    tenant_registry.register_runtime(key, new_db_url, label=label)

    return new_db_url


async def _provision_tenant_schema_mode(
    *, key: str, label: str, admin_email: str, admin_password: str,
) -> str:
    """Crea un tenant nuevo como schema PostgreSQL dentro de la DB compartida.

    Pasos (los 4 análogos al modo database, pero usando schemas):
      1. CREATE SCHEMA IF NOT EXISTS tenant_<key>
      2. Base.metadata.create_all() apuntando al schema nuevo
      3. Sembrar catálogos en ese schema
      4. Crear usuario admin en ese schema
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm

    from app.database import Base
    from app.tenant_strategy import schema_for_tenant, schema_translate_map_for_tenant

    settings = get_settings()
    base_url = settings.database_url
    schema_name = schema_for_tenant(key)

    print(f"-> [1/4] Creando schema '{schema_name}' en la DB compartida...")
    engine = create_async_engine(base_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

        print("-> [2/4] Creando tablas dentro del schema...")
        # Importamos todos los modelos para que estén registrados en
        # Base.metadata antes de crear las tablas.
        import app.models  # noqa: F401
        async with engine.begin() as conn:
            translated = await conn.execution_options(
                schema_translate_map=schema_translate_map_for_tenant(key),
            )
            await translated.run_sync(Base.metadata.create_all)

        print("-> [3/4] Sembrando catálogos (roles, estados, tipos)...")
        bound_engine = engine.execution_options(
            schema_translate_map=schema_translate_map_for_tenant(key),
        )
        sessionmaker = _asm(bind=bound_engine, class_=AsyncSession, expire_on_commit=False)
        async with sessionmaker() as session:
            # Las INSERTs van al schema correcto vía el translate map.
            await session.execute(text("""
                INSERT INTO roles (name) VALUES
                    ('CLIENTE'), ('TALLER'), ('TECNICO'),
                    ('OPERADOR'), ('ADMIN_TENANT'), ('SUPER_ADMIN')
                ON CONFLICT (name) DO NOTHING
            """))
            await session.execute(text("""
                INSERT INTO estados_solicitud (nombre) VALUES
                    ('REGISTRADA'), ('ASIGNADA'), ('EN_CAMINO'),
                    ('EN_ATENCION'), ('COMPLETADA'), ('CANCELADA'),
                    ('PENDIENTE'), ('PROPUESTA_TALLER'), ('RECHAZADA_TALLER')
                ON CONFLICT (nombre) DO NOTHING
            """))
            await session.execute(text("""
                INSERT INTO tipos_incidente (nombre, descripcion) VALUES
                    ('Choque',          'Colisión vehicular'),
                    ('Falla mecánica',  'Avería mecánica en ruta'),
                    ('Batería',         'Vehículo no arranca / batería descargada'),
                    ('Llanta ponchada', 'Neumático pinchado'),
                    ('Combustible',     'Tanque vacío')
                ON CONFLICT (nombre) DO NOTHING
            """))
            await session.commit()

        print(f"-> [4/4] Creando usuario admin '{admin_email}'...")
        from app.models.roles import Role
        from app.models.users import User
        async with sessionmaker() as session:
            role = await session.scalar(select(Role).where(Role.name == "ADMIN_TENANT"))
            if not role:
                raise RuntimeError("Role ADMIN_TENANT no fue sembrado")
            user = await session.scalar(select(User).where(User.email == admin_email))
            if user is None:
                user = User(email=admin_email, password_hash=hash_password(admin_password), is_active=True)
                user.roles.append(role)
                session.add(user)
            else:
                user.password_hash = hash_password(admin_password)
                if role not in user.roles:
                    user.roles.append(role)
            await session.commit()
    finally:
        await engine.dispose()

    # Registramos en el runtime registry para que el resto de la app lo vea.
    from app.services.tenant_registry import tenant_registry
    tenant_registry.register_runtime(key, base_url, label=label)
    return base_url


# ── CLI entrypoint ──────────────────────────────────────────────────────


def _main() -> None:
    if len(sys.argv) < 5:
        print(
            "Uso: python -m app.scripts.create_tenant <tenant_key> <label> <admin_email> <admin_password>",
            file=sys.stderr,
        )
        sys.exit(2)

    key, label, admin_email, admin_password = sys.argv[1:5]
    try:
        db_url = asyncio.run(
            provision_tenant(
                key=key,
                label=label,
                admin_email=admin_email,
                admin_password=admin_password,
            )
        )
    except Exception as exc:
        print(f"ERROR: Falló la creación del tenant: {exc}", file=sys.stderr)
        sys.exit(1)

    print()
    print("OK: Tenant provisionado correctamente")
    print(f"   key:          {key}")
    print(f"   label:        {label}")
    print(f"   db_url:       {db_url}")
    print(f"   admin email:  {admin_email}")
    print()
    print("⚠️  IMPORTANTE: agrega esta entrada a TENANT_DATABASES en backend/.env")
    print(f'   "{key}": "{db_url}"')


if __name__ == "__main__":
    # When invoked as a script (not via `-m`), make sure the package root is on
    # sys.path so absolute imports (`from app.config import ...`) resolve.
    if not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    _main()
