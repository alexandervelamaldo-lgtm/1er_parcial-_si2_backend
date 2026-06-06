"""
Tenant registry — central source of truth for which tenants exist and how to
reach their databases.

In the long run this should be backed by a small ``tenants`` table in a
dedicated metadata database so we can register new organizations at runtime
without restarting the API. For now we read the static ``TENANT_DATABASES``
mapping from settings, which keeps things simple for the exam while leaving
the door open for runtime registration via [register_runtime].

All other code should go through ``tenant_registry`` rather than poking at
``settings.tenant_databases`` directly, so when we migrate to a DB-backed
registry only one module changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from fastapi import HTTPException, status

from app.config import get_settings


@dataclass(slots=True)
class TenantInfo:
    """Public-facing metadata about a tenant. The DB URL is intentionally
    omitted because we don't want to leak it to the browser."""
    key: str
    label: str
    is_default: bool = False
    extras: dict[str, str] = field(default_factory=dict)


class _TenantRegistry:
    """Singleton holding the active tenant map. Thread-safe for runtime mutations."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._labels: dict[str, str] = {}
        self._suspended: set[str] = set()

    def list_keys(self) -> list[str]:
        return sorted(get_settings().tenant_databases.keys())

    def list_public(self) -> list[TenantInfo]:
        """Returns the tenants visible to the login page (key + label only)."""
        default = get_settings().default_tenant or "default"
        return [
            TenantInfo(
                key=key,
                label=self._labels.get(key, key.replace("_", " ").title()),
                is_default=(key == default),
            )
            for key in self.list_keys()
            if key not in self._suspended
        ]

    def exists(self, key: str) -> bool:
        return key in get_settings().tenant_databases and key not in self._suspended

    def require(self, key: str) -> str:
        """Returns the tenant key if known, else raises 404."""
        if not self.exists(key):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{key}' no existe o está suspendido",
            )
        return key

    def get_database_url(self, key: str) -> str:
        url = get_settings().tenant_databases.get(key)
        if not url:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{key}' no existe")
        return url

    # ── Runtime mutation ──────────────────────────────────────────────────

    def register_runtime(self, key: str, database_url: str, label: str | None = None) -> TenantInfo:
        """
        Adds a tenant at runtime — used by the ``create_tenant`` script and
        the admin "Crear tenant" panel. The change is in-memory only; for
        persistence the operator still has to update ``.env``.
        """
        with self._lock:
            settings = get_settings()
            settings.tenant_databases[key] = database_url
            if label:
                self._labels[key] = label
            self._suspended.discard(key)
        return TenantInfo(key=key, label=label or key.replace("_", " ").title())

    def suspend(self, key: str) -> None:
        with self._lock:
            self._suspended.add(key)

    def resume(self, key: str) -> None:
        with self._lock:
            self._suspended.discard(key)

    def set_label(self, key: str, label: str) -> None:
        with self._lock:
            self._labels[key] = label


# Module-level singleton — import this from routers / scripts.
tenant_registry = _TenantRegistry()
