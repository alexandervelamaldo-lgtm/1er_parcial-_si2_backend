"""Helpers para el modo schema-per-tenant.

Centraliza la decisión "¿estamos en modo schema?" para que el resto del
backend no tenga que conocer detalles de Postgres ni hardcodear nombres.

Nombres de schemas convencionales en modo `schema`:
  - tenant `default` → schema `tenant_default`
  - tenant `taller_xyz` → schema `tenant_taller_xyz`
  - control plane → schema `control_plane`

El prefijo `tenant_` evita colisión con schemas reservados de Postgres
(`public`, `information_schema`, etc.). Y el schema fijo `control_plane`
hace obvio dónde viven los super-admins.
"""

from __future__ import annotations

import re

from app.config import get_settings


SCHEMA_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TENANT_SCHEMA_PREFIX = "tenant_"
CONTROL_SCHEMA_NAME = "control_plane"


def using_schema_strategy() -> bool:
    """True cuando el sistema corre en modo schema-per-tenant."""
    settings = get_settings()
    return (settings.tenant_strategy or "database").strip().lower() == "schema"


def schema_for_tenant(tenant_key: str) -> str:
    """Devuelve el nombre de schema Postgres para un tenant key.

    Por ejemplo: `default` → `tenant_default`, `taller_norte` → `tenant_taller_norte`.

    Validamos el formato para evitar inyección SQL en lugares donde
    tenemos que interpolar el nombre del schema (ej. `CREATE SCHEMA`).
    """
    key = (tenant_key or "").strip().lower()
    if not key:
        raise ValueError("tenant_key vacío")
    schema = f"{TENANT_SCHEMA_PREFIX}{key}"
    if not SCHEMA_NAME_RE.match(schema):
        raise ValueError(f"tenant_key '{tenant_key}' produce schema inválido '{schema}'")
    return schema


def schema_translate_map_for_tenant(tenant_key: str) -> dict[str | None, str]:
    """Mapa de traducción SQLAlchemy para usar en `execution_options`.

    El default schema de los modelos es `None` (que Postgres resuelve a
    `public`). Lo redirigimos al schema del tenant correspondiente.
    """
    return {None: schema_for_tenant(tenant_key)}


def control_schema_translate_map() -> dict[str | None, str]:
    """Mapa para que las queries del control plane apunten al schema
    `control_plane` en lugar de `public`. Solo aplica en modo schema."""
    return {None: CONTROL_SCHEMA_NAME}
