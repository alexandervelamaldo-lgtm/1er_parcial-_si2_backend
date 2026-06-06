from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.control_plane.database import get_control_sessionmaker
from app.control_plane.models.super_admin import SuperAdmin
from app.database import get_tenant_sessionmaker
from app.models.users import User
from app.services.tenant_registry import tenant_registry
from app.utils.auth import verify_password


ACCOUNTS = [
    ("superadmin@platform.com", "SuperSecret123*"),
    ("admin.mecanica_general@platform.com", "AdminTenant123!"),
    ("admin.llaneros@platform.com", "AdminTenant123!"),
    ("admin.chapa_pintura@platform.com", "AdminTenant123!"),
    ("admin.vehiculos_nuevos_garantia@platform.com", "AdminTenant123!"),
    ("taller1.mecanica_general@platform.com", "Workshop123!"),
    ("taller1.llaneros@platform.com", "Workshop123!"),
    ("taller1.chapa_pintura@platform.com", "Workshop123!"),
    ("taller1.vehiculos_nuevos_garantia@platform.com", "Workshop123!"),
]


async def _check_super_admin(email: str, password: str) -> bool:
    sessionmaker = get_control_sessionmaker()
    async with sessionmaker() as session:
        admin = await session.scalar(select(SuperAdmin).where(SuperAdmin.email == email))
        return bool(admin and verify_password(password, admin.password_hash))


async def _check_tenants(email: str, password: str) -> list[str]:
    matches: list[str] = []
    for tenant in tenant_registry.list_keys():
        sessionmaker = get_tenant_sessionmaker(tenant)
        async with sessionmaker() as session:
            user = await session.scalar(select(User).where(User.email == email))
            if user and verify_password(password, user.password_hash):
                matches.append(tenant)
    return matches


async def main() -> None:
    for email, password in ACCOUNTS:
        ok_super = await _check_super_admin(email, password)
        tenants = await _check_tenants(email, password)
        if ok_super:
            print(f"{email}: OK super-admin")
        if tenants:
            print(f"{email}: OK tenants={','.join(tenants)}")
        if not ok_super and not tenants:
            print(f"{email}: FAIL (no match)")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

