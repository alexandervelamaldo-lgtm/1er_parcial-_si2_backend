"""Aplica manualmente la migración 019 a los tenants donde alembic se quedó en 018.

CONTEXTO: alembic.ini apunta a un solo DB (el principal, `default`), así que
`TENANT_KEY=<x> alembic upgrade head` NO redirige a la BD del tenant — alembic
sigue tocando `default`. En multi-tenant con BD por tenant cada DB necesita su
propio upgrade, y env.py todavía no traduce TENANT_KEY a la URL correcta.

Como solución de urgencia (la 019 es pequeña + idempotente), aplicamos sus DDLs
directamente vía `get_tenant_sessionmaker` (que SÍ respeta el tenant) a cada
DB cuya alembic_version siga en 018, y marcamos alembic_version manualmente.

TODO: arreglar `alembic/env.py` para que respete TENANT_KEY (entonces este
script se vuelve un `command.upgrade(cfg, 'head')` por tenant y se elimina).

Uso:
    python -m app.scripts.apply_migration_019
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.database import get_tenant_sessionmaker
from app.services.tenant_registry import tenant_registry


# DDL equivalente a `019_diag_categoria.upgrade()`. Cada statement es
# idempotente (IF NOT EXISTS) para que reaplicar el script sea inofensivo.
DDL_STATEMENTS = [
    "UPDATE solicitudes SET categoria_dano = 'general' WHERE categoria_dano IS NULL",
    "ALTER TABLE solicitudes ALTER COLUMN categoria_dano SET DEFAULT 'general'",
    "ALTER TABLE solicitudes ALTER COLUMN categoria_dano SET NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_solicitudes_categoria_dano ON solicitudes(categoria_dano)",
    "ALTER TABLE notificaciones ADD COLUMN IF NOT EXISTS diagnostico_categoria VARCHAR(80) NULL",
    "CREATE INDEX IF NOT EXISTS ix_notificaciones_diagnostico_categoria ON notificaciones(diagnostico_categoria)",
]
TARGET_REVISION = "019_diag_categoria"


async def main() -> None:
    tenants = tenant_registry.list_keys()
    print(f"Revisando {len(tenants)} tenant(s)...\n")
    for tenant in tenants:
        sm = get_tenant_sessionmaker(tenant)
        async with sm() as session:
            current = await session.scalar(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            if current == TARGET_REVISION:
                print(f"  {tenant}: ya en {current} — skip")
                continue
            print(f"  {tenant}: aplicando 019 (estaba en {current})...")
            for stmt in DDL_STATEMENTS:
                await session.execute(text(stmt))
            await session.execute(
                text("UPDATE alembic_version SET version_num = :v"),
                {"v": TARGET_REVISION},
            )
            await session.commit()
            print(f"  {tenant}: -> {TARGET_REVISION} OK")
    print("\nListo.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
