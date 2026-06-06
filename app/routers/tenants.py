"""
Tenant management endpoints.

Two surfaces:

* ``GET /tenants/public`` is **unauthenticated** — the login screen (web
  and mobile) calls it to populate the "Organización" dropdown. Only the
  key + label are returned; the database URL is never exposed.

* ``GET/POST/DELETE /admin/tenants*`` requires the ``SUPER_ADMIN`` role and
  is used by the cross-tenant control panel to onboard new organizations,
  suspend them, or take metrics about them. Creating a tenant *also*
  provisions its database (delegated to the ``create_tenant`` script).
"""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_tenant_sessionmaker
from app.dependencies.auth import get_current_user, get_role_names
from app.models.users import User
from app.services.tenant_registry import TenantInfo, tenant_registry

router = APIRouter(tags=["Tenants"])


# ── Schemas ─────────────────────────────────────────────────────────────


class TenantPublicResponse(BaseModel):
    key: str
    label: str
    is_default: bool


class TenantAdminResponse(BaseModel):
    key: str
    label: str
    is_default: bool
    user_count: int = 0
    solicitud_count: int = 0
    suspended: bool = False


class CreateTenantRequest(BaseModel):
    key: str = Field(min_length=3, max_length=40, description="Slug del tenant (ej: 'auxilio_norte')")
    label: str = Field(min_length=3, max_length=120, description="Nombre comercial")
    admin_email: str = Field(min_length=5, max_length=180)
    admin_password: str = Field(min_length=8, max_length=200)

    def normalized_key(self) -> str:
        return self.key.strip().lower()


_KEY_RE = re.compile(r"^[a-z0-9_]+$")


# ── Public endpoint (used by login forms) ───────────────────────────────


@router.get("/tenants/public", response_model=list[TenantPublicResponse])
async def list_public_tenants() -> list[TenantInfo]:
    """Returns the catalog of tenants visible to unauthenticated clients."""
    return tenant_registry.list_public()


# ── Super-admin endpoints ───────────────────────────────────────────────


def _require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    roles = get_role_names(current_user)
    if "SUPER_ADMIN" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo SUPER_ADMIN puede administrar tenants",
        )
    return current_user


async def _count_in_tenant(tenant_key: str, sql: str) -> int:
    """Runs a single COUNT(*) query against the given tenant's DB. Failures
    are swallowed and reported as ``-1`` so one broken tenant doesn't break
    the whole admin listing."""
    from sqlalchemy import text

    try:
        sessionmaker = get_tenant_sessionmaker(tenant_key)
        async with sessionmaker() as session:
            result = await session.execute(text(sql))
            return int(result.scalar() or 0)
    except Exception:
        return -1


@router.get("/admin/tenants", response_model=list[TenantAdminResponse])
async def list_admin_tenants(_admin: User = Depends(_require_super_admin)) -> list[TenantAdminResponse]:
    """Returns the full catalog with per-tenant user / solicitud counts."""
    tenants = tenant_registry.list_public()
    # Fan out the counts concurrently — saves seconds when there are many tenants.
    results = await asyncio.gather(
        *[
            asyncio.gather(
                _count_in_tenant(t.key, "SELECT COUNT(*) FROM users"),
                _count_in_tenant(t.key, "SELECT COUNT(*) FROM solicitudes"),
            )
            for t in tenants
        ],
        return_exceptions=False,
    )
    return [
        TenantAdminResponse(
            key=t.key,
            label=t.label,
            is_default=t.is_default,
            user_count=user_count,
            solicitud_count=solicitud_count,
            suspended=False,
        )
        for t, (user_count, solicitud_count) in zip(tenants, results, strict=True)
    ]


@router.post("/admin/tenants", response_model=TenantAdminResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant_endpoint(
    payload: CreateTenantRequest,
    _admin: User = Depends(_require_super_admin),
    _db: AsyncSession = Depends(get_db),
) -> TenantAdminResponse:
    """
    Provisions a brand-new tenant: a PostgreSQL database, the schema, the
    catalog data, and an initial admin user. Delegates to the same logic
    used by ``python -m app.scripts.create_tenant`` so the CLI and the UI
    cannot diverge.
    """
    key = payload.normalized_key()
    if not _KEY_RE.match(key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La clave del tenant solo admite letras minúsculas, números y guiones bajos",
        )
    if tenant_registry.exists(key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El tenant '{key}' ya existe",
        )

    # Lazy import to avoid a circular import between scripts and routers.
    from app.scripts.create_tenant import provision_tenant

    db_url = await provision_tenant(
        key=key,
        label=payload.label.strip(),
        admin_email=payload.admin_email.strip().lower(),
        admin_password=payload.admin_password,
    )
    tenant_registry.register_runtime(key, db_url, label=payload.label.strip())

    return TenantAdminResponse(
        key=key,
        label=payload.label.strip(),
        is_default=False,
        user_count=1,
        solicitud_count=0,
        suspended=False,
    )


@router.post(
    "/admin/tenants/{key}/suspend",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def suspend_tenant_endpoint(key: str, _admin: User = Depends(_require_super_admin)) -> Response:
    tenant_registry.require(key)
    tenant_registry.suspend(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/admin/tenants/{key}/resume",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def resume_tenant_endpoint(key: str, _admin: User = Depends(_require_super_admin)) -> Response:
    tenant_registry.resume(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
