"""Localiza un usuario en cualquier tenant y muestra su huella de datos.

Útil cuando un usuario reporta "no veo mis vehículos / solicitudes" — confirma
en qué tenant vive, si tiene record de Cliente y cuántos vehículos/solicitudes
están asociados a su cliente_id. Los endpoints filtran por cliente_id, así que
si el cliente está en otro tenant todo aparece vacío — eso es el aislamiento
multi-tenant funcionando, no un bug.

Uso:
    python -m app.scripts.locate_user <email>
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select

from app.database import get_tenant_sessionmaker
from app.models.clientes import Cliente
from app.models.solicitudes import Solicitud
from app.models.users import User
from app.models.vehiculos import Vehiculo
from app.services.tenant_registry import tenant_registry


async def main() -> None:
    if len(sys.argv) < 2:
        print("uso: python -m app.scripts.locate_user <email>")
        raise SystemExit(2)
    email = sys.argv[1].strip().lower()
    tenants = tenant_registry.list_keys()
    print(f"Buscando '{email}' en {len(tenants)} tenant(s)...\n")
    found_any = False
    for tenant in tenants:
        sessionmaker = get_tenant_sessionmaker(tenant)
        async with sessionmaker() as session:
            user = (
                await session.execute(
                    select(User).where(func.lower(User.email) == email)
                )
            ).scalar_one_or_none()
            if not user:
                continue
            found_any = True
            cliente = (
                await session.execute(
                    select(Cliente).where(Cliente.user_id == user.id)
                )
            ).scalar_one_or_none()
            cliente_id = cliente.id if cliente else None
            vehiculos = 0
            solicitudes = 0
            if cliente:
                vehiculos = await session.scalar(
                    select(func.count(Vehiculo.id)).where(
                        Vehiculo.cliente_id == cliente.id
                    )
                ) or 0
                solicitudes = await session.scalar(
                    select(func.count(Solicitud.id)).where(
                        Solicitud.cliente_id == cliente.id
                    )
                ) or 0
            print(
                f"  tenant={tenant}  user_id={user.id}  "
                f"cliente_id={cliente_id}  vehiculos={vehiculos}  solicitudes={solicitudes}"
            )
    if not found_any:
        print(f"  NO encontrado en ninguno de los {len(tenants)} tenants.")
        print("  → El usuario probablemente no existe (email mal escrito o nunca se registró).")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
