from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import get_tenant_sessionmaker
from app.models.roles import Role
from app.models.users import User
from app.utils.auth import hash_password


TENANTS = [
    "mecanica_general",
    "llaneros",
    "chapa_pintura",
    "vehiculos_nuevos_garantia",
]


PASSWORD = "AdminTenant123!"


async def _ensure_admin(*, tenant: str, email: str) -> None:
    sessionmaker = get_tenant_sessionmaker(tenant)
    async with sessionmaker() as session:
        role = await session.scalar(select(Role).where(Role.name == "ADMIN_TENANT"))
        if role is None:
            role = Role(name="ADMIN_TENANT")
            session.add(role)
            await session.flush()

        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash=hash_password(PASSWORD), is_active=True)
            user.roles.append(role)
            session.add(user)
        else:
            user.password_hash = hash_password(PASSWORD)
            if role not in user.roles:
                user.roles.append(role)

        await session.commit()


async def main() -> None:
    for tenant in TENANTS:
        email = f"admin.{tenant}@platform.com"
        await _ensure_admin(tenant=tenant, email=email)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

