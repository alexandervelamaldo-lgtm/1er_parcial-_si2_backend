from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import text

from app.control_plane.database import get_control_sessionmaker, init_control_db_schema
from app.scripts.create_tenant import provision_tenant


TENANTS: list[tuple[str, str]] = [
    ("mecanica_general", "Mecánica general"),
    ("llaneros", "Llaneros"),
    ("chapa_pintura", "Chapa y pintura"),
    ("vehiculos_nuevos_garantia", "Vehículos nuevos (garantía)"),
]

KEYWORDS: list[tuple[str, str, int]] = [
    ("llanta", "llaneros", 100),
    ("pinchadura", "llaneros", 100),
    ("pinchada", "llaneros", 100),
    ("ponchada", "llaneros", 100),
    ("vulcanizacion", "llaneros", 100),
    ("carroceria", "chapa_pintura", 90),
    ("chapa", "chapa_pintura", 90),
    ("pintura", "chapa_pintura", 90),
    ("garantia", "vehiculos_nuevos_garantia", 80),
    ("vehiculo nuevo", "vehiculos_nuevos_garantia", 80),
    ("motor", "mecanica_general", 10),
    ("electrico", "mecanica_general", 10),
    ("electronica", "mecanica_general", 10),
    ("mecanica", "mecanica_general", 10),
]


def _env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _load_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines(keepends=False)


def _set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(prefix):
            updated.append(prefix + value)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(prefix + value)
    return updated


def _parse_tenant_databases(lines: list[str]) -> dict[str, str]:
    for line in lines:
        if line.startswith("TENANT_DATABASES="):
            raw = line.split("=", 1)[1].strip()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return {str(k): str(v) for k, v in parsed.items()}
            except Exception:
                return {}
    return {}


async def _seed_keywords() -> None:
    await init_control_db_schema()
    sessionmaker = get_control_sessionmaker()
    async with sessionmaker() as session:
        for keyword, tenant_key, priority in KEYWORDS:
            await session.execute(
                text(
                    "INSERT INTO incident_tenant_keywords (keyword, tenant_key, priority) "
                    "VALUES (:keyword, :tenant_key, :priority) "
                    "ON CONFLICT (keyword) DO UPDATE SET tenant_key = EXCLUDED.tenant_key, priority = EXCLUDED.priority"
                ),
                {"keyword": keyword, "tenant_key": tenant_key, "priority": int(priority)},
            )
        await session.commit()


async def main() -> None:
    admin_email = os.environ.get("WORKSHOP_TENANTS_ADMIN_EMAIL", "admin@emergency.local")
    admin_password = os.environ.get("WORKSHOP_TENANTS_ADMIN_PASSWORD", "Admin123!")
    write_env = os.environ.get("WORKSHOP_TENANTS_WRITE_ENV", "true").strip().lower() in {"1", "true", "yes", "y"}

    tenant_urls: dict[str, str] = {}
    for key, label in TENANTS:
        db_url = await provision_tenant(
            key=key,
            label=label,
            admin_email=admin_email,
            admin_password=admin_password,
        )
        tenant_urls[key] = db_url

    await _seed_keywords()

    if write_env:
        path = _env_path()
        lines = _load_env_lines(path)
        current = _parse_tenant_databases(lines)
        merged = dict(current)
        merged.update(tenant_urls)
        lines = _set_env_value(lines, "TENANT_DATABASES", json.dumps(merged, ensure_ascii=False, separators=(",", ":")))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
