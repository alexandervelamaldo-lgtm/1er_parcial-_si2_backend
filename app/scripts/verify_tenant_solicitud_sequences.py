from __future__ import annotations

import asyncio
import re

from sqlalchemy import func, select, text

from app.database import get_tenant_sessionmaker
from app.models.solicitudes import Solicitud
from app.services.tenant_registry import tenant_registry
from app.tenant_strategy import schema_for_tenant, using_schema_strategy


_SEQ_RE = re.compile(r"nextval\('(?P<seq>[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)'::regclass\)")
_SAFE_SEQ = re.compile(r"^[a-zA-Z0-9_]+(?:\\.[a-zA-Z0-9_]+)?$")


async def _sequence_status(*, session, schema: str) -> tuple[str | None, int | None, bool | None]:
    default_expr = await session.scalar(
        text(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name = 'solicitudes'
              AND column_name = 'id'
            """
        ),
        {"schema": schema},
    )
    seq: str | None = None
    if default_expr:
        match = _SEQ_RE.search(str(default_expr))
        if match:
            seq = match.group("seq")

    if not seq:
        seq = await session.scalar(
            text("SELECT pg_get_serial_sequence(:tbl, 'id')"),
            {"tbl": f"{schema}.solicitudes"},
        )
    if not seq:
        seq = await session.scalar(
            text("SELECT pg_get_identity_sequence(:tbl, 'id')"),
            {"tbl": f"{schema}.solicitudes"},
        )
    if not seq:
        return None, None, None
    if not _SAFE_SEQ.match(seq):
        return None, None, None
    row = await session.execute(text(f"SELECT last_value, is_called FROM {seq}"))
    last_value, is_called = row.first()
    return seq, int(last_value), bool(is_called)


async def main() -> None:
    tenants = tenant_registry.list_keys()
    schema_mode = using_schema_strategy()
    print(f"tenant_strategy={'schema' if schema_mode else 'database'}")
    print()
    for tenant in tenants:
        sessionmaker = get_tenant_sessionmaker(tenant)
        schema = schema_for_tenant(tenant) if schema_mode else "public"
        async with sessionmaker() as session:
            db_name = await session.scalar(text("SELECT current_database()"))
            count, min_id, max_id = (
                await session.execute(select(func.count(Solicitud.id), func.min(Solicitud.id), func.max(Solicitud.id)))
            ).first()
            id_meta = (
                await session.execute(
                    text(
                        """
                        SELECT table_schema, column_default, is_identity, identity_generation
                        FROM information_schema.columns
                        WHERE table_name = 'solicitudes' AND column_name = 'id'
                        """
                    )
                )
            ).first()
            id_meta_label = None
            if id_meta:
                id_meta_label = f"schema={id_meta[0]} default={id_meta[1]} identity={id_meta[2]} gen={id_meta[3]}"
            seq_name, last_value, is_called = await _sequence_status(session=session, schema=schema)
            if last_value is None:
                next_id = None
            else:
                next_id = last_value + 1 if is_called else last_value
            ok_new_tenant = count == 0 and next_id == 1
            ok_seq_aligned = max_id is None or next_id is None or next_id >= int(max_id) + 1
            status = []
            if ok_new_tenant:
                status.append("OK:new-tenant-starts-at-1")
            if ok_seq_aligned:
                status.append("OK:seq>=max+1")
            if not status:
                status.append("WARN:seq-mismatch-or-unknown")
            print(
                f"{tenant}: db={db_name} solicitudes={count} min={min_id} max={max_id} next_id={next_id} seq={seq_name} "
                f"({' '.join(status)})"
            )
            if id_meta_label and seq_name is None:
                print(f"  id-meta: {id_meta_label}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
