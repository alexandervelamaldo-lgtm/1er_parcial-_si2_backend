from __future__ import annotations

import asyncio
import os

from sqlalchemy import select, text

from app.database import get_tenant_sessionmaker
from app.models.roles import Role
from app.models.users import User
from app.utils.auth import hash_password


TENANT = os.environ.get("DEMO_SUPER_ADMIN_TENANT", "default")
EMAIL = os.environ.get("DEMO_SUPER_ADMIN_EMAIL", "superadmin@emergency.com")
PASSWORD = os.environ.get("DEMO_SUPER_ADMIN_PASSWORD", "SuperAdmin123!")


async def main() -> None:
    Session = get_tenant_sessionmaker(TENANT)
    async with Session() as session:
        await session.execute(
            text("INSERT INTO roles (name) VALUES ('SUPER_ADMIN') ON CONFLICT (name) DO NOTHING")
        )
        await session.commit()

        role = await session.scalar(select(Role).where(Role.name == "SUPER_ADMIN"))
        if not role:
            raise RuntimeError("No se pudo crear/encontrar el rol SUPER_ADMIN")

        user = await session.scalar(select(User).where(User.email == EMAIL))
        if user is None:
            user = User(
                email=EMAIL,
                password_hash=hash_password(PASSWORD),
                is_active=True,
            )
            user.roles.append(role)
            session.add(user)
        else:
            user.password_hash = hash_password(PASSWORD)
            if role not in user.roles:
                user.roles.append(role)

        await session.commit()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
