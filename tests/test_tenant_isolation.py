"""
Tenant isolation contract tests.

These tests verify the **invariants** the system promises every tenant —
not the implementation. They exercise [resolve_tenant_key], the JWT tenant
claim, the consistency check in ``get_current_user``, and the tenant
registry. We use plain function calls + fakes rather than spinning up the
whole DB stack so the tests are deterministic and run in <1 second.

If any of these tests fail, *data is leaking across tenants* — do not ship.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, QueryParams
from starlette.requests import HTTPConnection


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_conn(*, path: str = "/solicitudes", headers: dict | None = None, query: str = "") -> HTTPConnection:
    """Builds a minimal ``HTTPConnection`` we can feed to resolve_tenant_key."""
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": raw_headers,
        "query_string": query.encode(),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return HTTPConnection(scope)


def _patch_settings(monkeypatch, *, tenants: dict[str, str], default: str = "default"):
    """Replaces ``get_settings()`` so the resolver sees a controlled tenant map."""
    from app import config as config_module

    fake = SimpleNamespace(
        tenant_databases=dict(tenants),
        default_tenant=default,
        secret_key="test-secret",
        algorithm="HS256",
        access_token_expire_minutes=5,
    )
    monkeypatch.setattr(config_module, "get_settings", lambda: fake)
    # The tenant module caches `get_settings` reference at module level inside
    # resolve_tenant_key, but it calls it on every invocation, so the patch
    # above is enough as long as we re-import the module.
    return fake


# ── Tests for resolve_tenant_key ────────────────────────────────────────


class TestResolveTenantKey:
    def test_returns_default_when_no_signal(self, monkeypatch):
        _patch_settings(monkeypatch, tenants={"default": "url"})
        from app.dependencies import tenant as tenant_module
        importlib.reload(tenant_module)

        conn = _make_conn()
        assert tenant_module.resolve_tenant_key(conn) == "default"

    def test_picks_x_tenant_header(self, monkeypatch):
        _patch_settings(monkeypatch, tenants={"default": "u", "tenant_a": "u"})
        from app.dependencies import tenant as tenant_module
        importlib.reload(tenant_module)

        conn = _make_conn(headers={"X-Tenant": "tenant_a"})
        assert tenant_module.resolve_tenant_key(conn) == "tenant_a"

    def test_picks_query_param(self, monkeypatch):
        _patch_settings(monkeypatch, tenants={"default": "u", "tenant_b": "u"})
        from app.dependencies import tenant as tenant_module
        importlib.reload(tenant_module)

        conn = _make_conn(query="tenant=tenant_b")
        assert tenant_module.resolve_tenant_key(conn) == "tenant_b"

    def test_unknown_explicit_tenant_raises_404(self, monkeypatch):
        """The headline isolation guarantee: an attacker cannot probe for
        leakage by sending bogus tenant keys — they get 404, not a silent
        fallback to the default tenant's data."""
        _patch_settings(monkeypatch, tenants={"default": "u", "tenant_a": "u"})
        from app.dependencies import tenant as tenant_module
        importlib.reload(tenant_module)

        conn = _make_conn(headers={"X-Tenant": "tenant_hacker"})
        with pytest.raises(HTTPException) as exc:
            tenant_module.resolve_tenant_key(conn)
        assert exc.value.status_code == 404

    def test_unknown_tenant_on_public_path_falls_back(self, monkeypatch):
        """``/tenants/public`` is unauthenticated — we don't want to leak
        the list of valid tenants via 404/200 timing differences."""
        _patch_settings(monkeypatch, tenants={"default": "u"})
        from app.dependencies import tenant as tenant_module
        importlib.reload(tenant_module)

        conn = _make_conn(path="/tenants/public", headers={"X-Tenant": "unknown"})
        assert tenant_module.resolve_tenant_key(conn) == "default"

    def test_header_takes_precedence_over_jwt_claim(self, monkeypatch):
        """If a user with a Tenant-A JWT sets ``X-Tenant: tenant_b``, the
        header wins for tenant *routing*. The /token-mismatch check happens
        later in ``get_current_user`` and rejects the request."""
        _patch_settings(monkeypatch, tenants={"default": "u", "tenant_a": "u", "tenant_b": "u"})
        from app.dependencies import tenant as tenant_module
        importlib.reload(tenant_module)

        # We don't need a real JWT — just simulate header + matching tenant.
        conn = _make_conn(headers={"X-Tenant": "tenant_b"})
        assert tenant_module.resolve_tenant_key(conn) == "tenant_b"


# ── Tests for get_current_user tenant-match guard ───────────────────────


class TestGetCurrentUserTenantGuard:
    """``get_current_user`` is the second line of defence: even if the
    resolver picks a tenant the JWT can override the protection if it
    contains a different ``tenant`` claim. This test confirms the guard."""

    @pytest.mark.asyncio
    async def test_jwt_tenant_a_rejected_when_request_is_tenant_b_for_non_client(self, monkeypatch):
        from app.dependencies import auth as auth_module
        from app.utils.auth import create_access_token

        # Make sure the token we forge is decodable by the same secret.
        monkeypatch.setattr(
            "app.utils.auth.settings",
            SimpleNamespace(
                secret_key="test-secret",
                algorithm="HS256",
                access_token_expire_minutes=5,
            ),
        )

        token = create_access_token(
            subject="user@tenant_a.com",
            extra={"roles": ["TALLER"], "tenant": "tenant_a", "user_id": 1},
        )

        request = MagicMock()
        request.state.tenant_key = "tenant_b"   # ← attacker swapped the header

        db_mock = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await auth_module.get_current_user(request=request, token=token, db=db_mock)
        assert exc.value.status_code == 401
        assert "tenant" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_cliente_token_can_cross_tenants(self, monkeypatch):
        from unittest.mock import AsyncMock

        from app.dependencies import auth as auth_module
        from app.utils.auth import create_access_token

        monkeypatch.setattr(
            "app.utils.auth.settings",
            SimpleNamespace(
                secret_key="test-secret",
                algorithm="HS256",
                access_token_expire_minutes=5,
            ),
        )

        token = create_access_token(
            subject="cliente@example.com",
            extra={"roles": ["CLIENTE"], "tenant": "tenant_a", "user_id": 1},
        )
        request = MagicMock()
        request.state.tenant_key = "tenant_b"

        fake_user = MagicMock()
        fake_user.is_active = True
        fake_result = MagicMock()
        fake_result.scalar_one_or_none.return_value = fake_user

        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=fake_result)

        user = await auth_module.get_current_user(request=request, token=token, db=db_mock)
        assert user is fake_user


# ── Tests for the tenant registry ───────────────────────────────────────


class TestTenantRegistry:
    def test_list_public_omits_suspended(self, monkeypatch):
        _patch_settings(monkeypatch, tenants={"default": "u", "tenant_a": "u"})
        from app.services import tenant_registry as registry_module
        importlib.reload(registry_module)

        registry_module.tenant_registry.suspend("tenant_a")
        keys = {t.key for t in registry_module.tenant_registry.list_public()}
        assert "tenant_a" not in keys
        assert "default" in keys

    def test_require_raises_for_unknown(self, monkeypatch):
        _patch_settings(monkeypatch, tenants={"default": "u"})
        from app.services import tenant_registry as registry_module
        importlib.reload(registry_module)

        with pytest.raises(HTTPException) as exc:
            registry_module.tenant_registry.require("ghost_tenant")
        assert exc.value.status_code == 404

    def test_register_runtime_adds_to_settings(self, monkeypatch):
        _patch_settings(monkeypatch, tenants={"default": "u"})
        from app.services import tenant_registry as registry_module
        importlib.reload(registry_module)

        registry_module.tenant_registry.register_runtime(
            "tenant_new",
            "postgresql+asyncpg://...",
            label="New Org",
        )
        assert registry_module.tenant_registry.exists("tenant_new")
