from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.models.clientes import Cliente
from app.models.solicitudes import Solicitud
from app.database import get_tenant_sessionmaker
from app.models.users import User
from app.routers.gestion_solicitudes.solicitudes import (
    _list_client_requests_across_tenants,
    _open_solicitud_session,
)
from app.services.tenant_registry import tenant_registry


async def main(solicitud_id: int) -> None:
    owner_email: str | None = None
    owner_tenant: str | None = None

    for tenant in tenant_registry.list_keys():
        sessionmaker = get_tenant_sessionmaker(tenant)
        async with sessionmaker() as session:
            session.info["tenant_key"] = tenant
            solicitud = await session.scalar(select(Solicitud).where(Solicitud.id == solicitud_id))
            if solicitud is None:
                continue
            user = await session.scalar(
                select(User)
                .join(Cliente, Cliente.user_id == User.id)
                .where(Cliente.id == solicitud.cliente_id)
            )
            owner_email = user.email if user else None
            owner_tenant = tenant
            break

    if not owner_email or not owner_tenant:
        print(f"Solicitud #{solicitud_id} no encontrada en ningún tenant.")
        return

    default_sessionmaker = get_tenant_sessionmaker("default")
    async with default_sessionmaker() as default_db:
        default_db.info["tenant_key"] = "default"
        current_user = await default_db.scalar(select(User).where(User.email == owner_email))
        if current_user is None:
            print(f"El cliente {owner_email} no existe en default.")
            return

        aggregated = await _list_client_requests_across_tenants(
            db=default_db,
            current_user=current_user,
            diagnostico_categoria=None,
            only_active=False,
        )
        hit = next((item for item in aggregated if item.id == solicitud_id and item.tenant_key == owner_tenant), None)
        async with _open_solicitud_session(default_db, solicitud_id, current_user, None, None, None) as (
            solicitud,
            _session,
            _usuario_id,
            cliente_id,
            _tecnico_id,
            _taller_id,
        ):
            print(f"owner_email={owner_email}")
            print(f"owner_tenant={owner_tenant}")
            print(f"aggregated_visible={'yes' if hit else 'no'}")
            print(f"resolved_cross_tenant={'yes' if solicitud is not None else 'no'}")
            print(f"resolved_cliente_id={cliente_id}")


if __name__ == "__main__":
    solicitud_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    raise SystemExit(asyncio.run(main(solicitud_id)))
