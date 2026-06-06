from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import get_tenant_sessionmaker
from app.models.association_tables import user_roles
from app.models.roles import Role
from app.models.users import User


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
            rows = (
                await session.execute(
                    select(User.email)
                    .select_from(User)
                    .join(user_roles, user_roles.c.user_id == User.id)
                    .join(Role, Role.id == user_roles.c.role_id)
                    .where(Role.name == "ADMIN_TENANT")
                    .order_by(User.email)
                )
            ).scalars().all()
            print(f"{tenant}: {', '.join(rows) if rows else '(sin admin)'}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

