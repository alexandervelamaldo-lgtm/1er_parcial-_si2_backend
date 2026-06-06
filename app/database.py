import asyncio
import sys
from collections.abc import AsyncGenerator

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from starlette.requests import HTTPConnection

from app.config import get_settings
from app.dependencies.tenant import resolve_tenant_key


settings = get_settings()


class Base(DeclarativeBase):
    pass


_engines: dict[str, AsyncEngine] = {}
_sessionmakers: dict[str, async_sessionmaker[AsyncSession]] = {}

# En modo schema-per-tenant TODOS los tenants comparten el mismo engine
# (apuntando a la única DB). El aislamiento se hace por schema usando
# `execution_options(schema_translate_map=...)` al abrir cada session.
_SHARED_ENGINE_KEY = "__shared__"


def _build_pg_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        future=True,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=10,
    )


def _get_engine(tenant: str) -> AsyncEngine:
    # Import local para evitar ciclos en pruebas (tenant_strategy depende
    # de config, igual que database.py).
    from app.tenant_strategy import using_schema_strategy

    if using_schema_strategy():
        # Modo schema: un único engine compartido para todos los tenants.
        engine = _engines.get(_SHARED_ENGINE_KEY)
        if engine:
            return engine
        database_url = settings.database_url
        if database_url.startswith("postgresql") or database_url.startswith("postgres"):
            engine = _build_pg_engine(database_url)
        else:
            engine = create_async_engine(database_url, future=True, echo=False)
        _engines[_SHARED_ENGINE_KEY] = engine
        return engine

    # Modo database (legacy): un engine por tenant.
    engine = _engines.get(tenant)
    if engine:
        return engine
    database_url = settings.tenant_databases.get(tenant) or settings.database_url
    if database_url.startswith("postgresql") or database_url.startswith("postgres"):
        engine = _build_pg_engine(database_url)
    else:
        engine = create_async_engine(database_url, future=True, echo=False)
    _engines[tenant] = engine
    return engine


def _get_sessionmaker(tenant: str) -> async_sessionmaker[AsyncSession]:
    from app.tenant_strategy import (
        schema_translate_map_for_tenant,
        using_schema_strategy,
    )

    # En modo schema, cacheamos un sessionmaker por tenant porque el
    # schema_translate_map difiere, aunque el engine subyacente sea uno solo.
    cache_key = f"schema:{tenant}" if using_schema_strategy() else tenant
    maker = _sessionmakers.get(cache_key)
    if maker:
        return maker

    engine = _get_engine(tenant)

    if using_schema_strategy():
        # execution_options aplica el schema_translate_map a TODAS las
        # operaciones del session. SQLAlchemy reescribe transparentemente
        # `SELECT FROM users` → `SELECT FROM tenant_X.users`.
        bound = engine.execution_options(
            schema_translate_map=schema_translate_map_for_tenant(tenant),
        )
        maker = async_sessionmaker(
            bind=bound, class_=AsyncSession, expire_on_commit=False,
        )
    else:
        maker = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False,
        )
    _sessionmakers[cache_key] = maker
    return maker


def get_tenant_sessionmaker(tenant: str) -> async_sessionmaker[AsyncSession]:
    return _get_sessionmaker(tenant)


AsyncSessionLocal = _get_sessionmaker(settings.default_tenant or "default")


async def get_db(conn: HTTPConnection) -> AsyncGenerator[AsyncSession, None]:
    tenant = resolve_tenant_key(conn)
    conn.state.tenant_key = tenant
    sessionmaker = _get_sessionmaker(tenant)
    async with sessionmaker() as session:
        session.info["tenant_key"] = tenant
        yield session
