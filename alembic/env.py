import json
from logging.config import fileConfig
import os
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.database import Base
from app.models import *  # noqa: F403


config = context.config


def _normalize_database_url(database_url: str) -> str:
    normalized = (database_url or "").strip()
    if normalized.startswith("postgres://"):
        return normalized.replace("postgres://", "postgresql+asyncpg://", 1)
    if normalized.startswith("postgresql://") and "+asyncpg" not in normalized:
        return normalized.replace("postgresql://", "postgresql+asyncpg://", 1)
    return normalized


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_database_url_for_tenant(default_url: str) -> str:
    tenant_key = (os.getenv("TENANT_KEY") or "").strip()
    if not tenant_key:
        return default_url
    raw_tenants = (os.getenv("TENANT_DATABASES") or "").strip()
    if not raw_tenants:
        return default_url
    try:
        parsed = json.loads(raw_tenants)
    except json.JSONDecodeError:
        return default_url
    if not isinstance(parsed, dict):
        return default_url
    tenant_url = str(parsed.get(tenant_key, "")).strip()
    return _normalize_database_url(tenant_url) or default_url


_load_env_file()
# Prefer the .env / OS environment variable over the static alembic.ini value.
# The .ini is just a fallback template — the source of truth for credentials
# is .env, which is also what the FastAPI app reads at runtime. Without this
# inversion, alembic would talk to a different DB than the running app.
database_url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or ""
database_url = _normalize_database_url(database_url)
database_url = _resolve_database_url_for_tenant(database_url)
if not database_url:
    raise RuntimeError("DATABASE_URL es obligatoria para ejecutar migraciones")
config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio

    asyncio.run(run_migrations_online())
