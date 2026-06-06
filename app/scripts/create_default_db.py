from __future__ import annotations

import asyncio
from urllib.parse import urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings


def _admin_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path="/postgres"))


def _database_name(db_url: str) -> str:
    parsed = urlparse(db_url)
    name = (parsed.path or "").lstrip("/").strip()
    if not name:
        raise ValueError("DATABASE_URL no tiene nombre de base de datos en el path")
    return name


async def main() -> None:
    settings = get_settings()
    db_name = _database_name(settings.database_url)

    engine = create_async_engine(_admin_url(settings.database_url), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            )
            if exists:
                print(f"OK: la base de datos '{db_name}' ya existe")
                return
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            print(f"OK: creada la base de datos '{db_name}'")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
