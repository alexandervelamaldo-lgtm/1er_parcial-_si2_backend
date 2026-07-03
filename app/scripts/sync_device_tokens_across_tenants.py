from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import get_tenant_sessionmaker
from app.models.device_tokens import UserDeviceToken
from app.models.users import User
from app.services.tenant_registry import tenant_registry


async def main() -> None:
    default_sessionmaker = get_tenant_sessionmaker("default")
    async with default_sessionmaker() as default_db:
        users = (
            await default_db.execute(
                select(User).where(User.device_tokens.any())
            )
        ).scalars().all()
        if not users:
            print("No hay usuarios con device tokens en default.")
            return

        for source_user in users:
            tokens = (
                await default_db.execute(
                    select(UserDeviceToken).where(UserDeviceToken.user_id == source_user.id)
                )
            ).scalars().all()
            for tenant in tenant_registry.list_keys():
                if tenant == "default":
                    continue
                sessionmaker = get_tenant_sessionmaker(tenant)
                async with sessionmaker() as tenant_db:
                    tenant_db.info["tenant_key"] = tenant
                    target_user = await tenant_db.scalar(select(User).where(User.email == source_user.email))
                    if target_user is None:
                        continue
                    created = 0
                    for token in tokens:
                        existing = await tenant_db.scalar(
                            select(UserDeviceToken).where(
                                UserDeviceToken.user_id == target_user.id,
                                UserDeviceToken.token == token.token,
                            )
                        )
                        if existing:
                            existing.plataforma = token.plataforma
                            continue
                        tenant_db.add(
                            UserDeviceToken(
                                user_id=target_user.id,
                                token=token.token,
                                plataforma=token.plataforma,
                            )
                        )
                        created += 1
                    if created:
                        await tenant_db.commit()
                        print(f"{source_user.email} -> {tenant}: +{created} tokens")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

