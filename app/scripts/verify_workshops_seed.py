from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.database import get_tenant_sessionmaker


TENANTS = [
    "mecanica_general",
    "llaneros",
    "chapa_pintura",
    "vehiculos_nuevos_garantia",
]


async def main() -> None:
    for tenant in TENANTS:
        sessionmaker = get_tenant_sessionmaker(tenant)
        async with sessionmaker() as session:
            users = await session.scalar(text("select count(*) from users"))
            talleres = await session.scalar(text("select count(*) from talleres"))
            print(f"{tenant}: users={users} talleres={talleres}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

