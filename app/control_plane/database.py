"""Control plane database — vive ENCIMA del modelo multi-tenant.

Aquí almacenamos entidades que NO pertenecen a ninguna organización:
  - SuperAdmin: usuarios con permisos globales sobre tenants.
  - SuperAdminAuditLog: auditoría de operaciones cross-tenant.

Diseño:
  - Engine y sessionmaker propios — totalmente separados de
    `app.database` que maneja tenants.
  - Si `CONTROL_DATABASE_URL` está vacío, derivamos uno automáticamente
    cambiando el nombre de la database principal a `<original>_control`.
    Esto evita configuración manual en entornos de desarrollo.
  - Las tablas se crean con `Base.metadata.create_all` al startup
    (no usamos Alembic separado para no duplicar tooling). Para el
    proyecto de tamaño actual es suficiente; en producción a gran
    escala conviene migrarlo a su propio alembic.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator
from urllib.parse import urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)


class ControlBase(DeclarativeBase):
    """Base SQLAlchemy SEPARADA de la de tenants. Modelos definidos sobre
    esta base viven SOLO en la control DB."""
    pass


def _derive_control_url(database_url: str) -> str:
    """Deriva la URL de la control DB cambiando el nombre de la base
    de `<x>` a `<x>_control`. Mantiene host, credenciales, driver."""
    if not database_url:
        return ""
    # Postgres URL típico: postgresql+asyncpg://user:pass@host:port/dbname
    # Sustituimos solo el último segmento.
    match = re.match(r"^(.+?)/([^/?]+)(\?.*)?$", database_url)
    if not match:
        return database_url
    prefix, dbname, query = match.group(1), match.group(2), match.group(3) or ""
    if dbname.endswith("_control"):
        # Ya es una URL de control — devolvemos tal cual.
        return database_url
    return f"{prefix}/{dbname}_control{query}"


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _resolved_control_url(settings) -> str:
    """URL efectiva para la control DB.

    - Modo `schema`: vivimos dentro de la MISMA DB que los tenants (solo
      hay 1 DB en Render free). Usamos `settings.database_url` y separamos
      por schema vía `schema_translate_map`.
    - Modo `database`: usamos `control_database_url` si está, o derivamos
      `<dbname>_control` automáticamente.
    """
    from app.tenant_strategy import using_schema_strategy

    if using_schema_strategy():
        # Una sola DB en proveedores free tier — el control plane comparte
        # el motor pero queda aislado en su propio schema.
        url = settings.database_url.strip()
        if not url:
            raise RuntimeError("DATABASE_URL es obligatoria en modo schema.")
        return url

    # Modo database: como antes.
    url = (settings.control_database_url or "").strip()
    if not url:
        url = _derive_control_url(settings.database_url)
    if not url:
        raise RuntimeError("No se pudo determinar la URL de la control DB.")
    return url


def get_control_engine() -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine
    settings = get_settings()
    url = _resolved_control_url(settings)
    logger.info("control_plane — engine inicializado (DB %s)", _sanitize_url_for_log(url))
    _engine = create_async_engine(
        url, future=True, echo=False,
        pool_pre_ping=True, pool_recycle=1800, pool_size=2, max_overflow=4,
    )
    return _engine


def get_control_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is not None:
        return _sessionmaker
    from app.tenant_strategy import control_schema_translate_map, using_schema_strategy

    engine = get_control_engine()
    if using_schema_strategy():
        # En modo schema, las tablas del control plane viven en su propio
        # schema dentro de la DB compartida. Aplicamos el translate map
        # para que las queries lo apunten transparentemente.
        bound = engine.execution_options(schema_translate_map=control_schema_translate_map())
        _sessionmaker = async_sessionmaker(
            bind=bound, class_=AsyncSession, expire_on_commit=False,
        )
    else:
        _sessionmaker = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False,
        )
    return _sessionmaker


async def get_control_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency para FastAPI — análogo a `get_db()` pero sin resolver
    tenant. Las rutas que viven en el control plane usan esto."""
    sessionmaker = get_control_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def _create_database_if_missing(control_url: str) -> None:
    """Crea la database física en Postgres si no existe.

    Se conecta a la database de mantenimiento `postgres` (que SIEMPRE
    existe en una instalación de Postgres) y ejecuta `CREATE DATABASE`
    sobre el nombre derivado. Idempotente: si ya existe, es no-op.

    Solo aplica a Postgres. Para SQLite (tests) la DB se crea con el
    primer `connect()` automáticamente — no entramos a esta función.
    """
    parsed = urlparse(control_url)
    if not parsed.scheme.startswith("postgres"):
        return  # SQLite, MySQL, etc. — manejan creación distinto.
    target_dbname = (parsed.path or "/").lstrip("/")
    if not target_dbname:
        return
    # URL apuntando a la database `postgres` de mantenimiento.
    admin_url = urlunparse(parsed._replace(path="/postgres"))

    # CREATE DATABASE no puede correr dentro de transacción.
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target_dbname},
            )
            if exists:
                return
            # SQL injection safety: dbname es derivado de
            # settings.database_url, no de input de usuario.
            await conn.execute(text(f'CREATE DATABASE "{target_dbname}"'))
            logger.info("control_plane — database '%s' creada.", target_dbname)
    finally:
        await admin_engine.dispose()


async def init_control_db_schema() -> None:
    """Crea la database / schema + tablas del control plane si no existen.

    Idempotente. En modo `database` crea una DB física separada.
    En modo `schema` crea un schema `control_plane` dentro de la DB
    compartida.
    """
    # Import local para asegurar que los modelos están registrados en
    # ControlBase.metadata antes de crear las tablas.
    from app.control_plane.models import super_admin as _super_admin  # noqa: F401
    from app.control_plane.models import audit as _audit              # noqa: F401
    from app.control_plane.models import incident_tenant_keyword as _incident_tenant_keyword
    from app.tenant_strategy import (
        CONTROL_SCHEMA_NAME,
        control_schema_translate_map,
        using_schema_strategy,
    )

    settings = get_settings()

    if using_schema_strategy():
        # Modo schema: la DB ya existe (es la principal). Solo asegurar
        # que el schema `control_plane` existe y crear las tablas adentro.
        engine = get_control_engine()
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{CONTROL_SCHEMA_NAME}"'))
            # Aplicamos el translate map a la conexión usada por create_all,
            # así las tablas se crean en el schema correcto.
            translated = await conn.execution_options(
                schema_translate_map=control_schema_translate_map(),
            )
            await translated.run_sync(ControlBase.metadata.create_all)
        logger.info("control_plane — schema '%s' inicializado.", CONTROL_SCHEMA_NAME)
        return

    # Modo database (legacy): DB física separada.
    url = (settings.control_database_url or "").strip() or _derive_control_url(settings.database_url)
    await _create_database_if_missing(url)
    engine = get_control_engine()
    async with engine.begin() as conn:
        await conn.run_sync(ControlBase.metadata.create_all)
    logger.info("control_plane — schema inicializado.")


def _sanitize_url_for_log(url: str) -> str:
    """Oculta credenciales antes de loggear la URL."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
