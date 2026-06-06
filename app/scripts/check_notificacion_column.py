"""Verifica directamente en cada tenant DB si la columna
`notificaciones.diagnostico_categoria` existe.

Útil cuando alembic_version dice "estoy en HEAD" pero el SQL del runtime sigue
reventando con UndefinedColumnError — el caso típico es que la migración se
marcó como aplicada sin ejecutar el DDL (rollback parcial, intervención manual,
etc.), o que el tenant nunca corrió esa migración pese a lo que dice el
control_plane.

Uso:
    python -m app.scripts.check_notificacion_column
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.database import get_tenant_sessionmaker
from app.services.tenant_registry import tenant_registry


async def main() -> None:
    tenants = tenant_registry.list_keys()
    for tenant in tenants:
        sm = get_tenant_sessionmaker(tenant)
        async with sm() as session:
            cols_result = await session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'notificaciones'
                    ORDER BY ordinal_position
                    """
                )
            )
            cols = [r[0] for r in cols_result.all()]
            ver = await session.scalar(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            has_diag = "diagnostico_categoria" in cols
            mark = "OK" if has_diag else "FALTA"
            print(
                f"{tenant}: alembic={ver} cols={len(cols)} has_diag={has_diag} [{mark}]"
            )
            if not has_diag:
                print(f"  -> columnas: {cols}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
