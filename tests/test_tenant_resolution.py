"""Pruebas unitarias para la resolución de tenant.

Valida la lógica de resolve_tenant_key: header > query param > token > default.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.dependencies.tenant import resolve_tenant_key


def _make_conn(
    *,
    x_tenant: str = "",
    x_tenant_id: str = "",
    query_tenant: str = "",
    auth_header: str = "",
) -> MagicMock:
    conn = MagicMock()
    headers: dict[str, str] = {}
    if x_tenant:
        headers["x-tenant"] = x_tenant
    if x_tenant_id:
        headers["x-tenant-id"] = x_tenant_id
    if auth_header:
        headers["authorization"] = auth_header
    conn.headers.get = lambda key, default="": headers.get(key, default)
    conn.query_params.get = lambda key, default="": query_tenant if key == "tenant" else default
    return conn


def _settings_with_tenants(*tenants: str):
    mock_settings = MagicMock()
    mock_settings.tenant_databases = {t: f"postgresql+asyncpg://db/{t}" for t in tenants}
    mock_settings.default_tenant = "default"
    return mock_settings


@patch("app.dependencies.tenant.get_settings")
def test_resuelve_desde_header_x_tenant(mock_get_settings) -> None:
    mock_get_settings.return_value = _settings_with_tenants("taller_norte", "default")
    conn = _make_conn(x_tenant="taller_norte")
    assert resolve_tenant_key(conn) == "taller_norte"


@patch("app.dependencies.tenant.get_settings")
def test_resuelve_desde_header_x_tenant_id(mock_get_settings) -> None:
    mock_get_settings.return_value = _settings_with_tenants("taller_sur", "default")
    conn = _make_conn(x_tenant_id="taller_sur")
    assert resolve_tenant_key(conn) == "taller_sur"


@patch("app.dependencies.tenant.get_settings")
def test_resuelve_desde_query_param(mock_get_settings) -> None:
    mock_get_settings.return_value = _settings_with_tenants("taller_centro", "default")
    conn = _make_conn(query_tenant="taller_centro")
    assert resolve_tenant_key(conn) == "taller_centro"


@patch("app.dependencies.tenant.get_settings")
def test_retorna_default_cuando_tenant_no_registrado(mock_get_settings) -> None:
    mock_get_settings.return_value = _settings_with_tenants("default")
    conn = _make_conn(x_tenant="taller_inexistente")
    assert resolve_tenant_key(conn) == "default"


@patch("app.dependencies.tenant.get_settings")
def test_retorna_default_sin_headers_ni_query(mock_get_settings) -> None:
    mock_get_settings.return_value = _settings_with_tenants("default")
    conn = _make_conn()
    assert resolve_tenant_key(conn) == "default"


@patch("app.dependencies.tenant.get_settings")
def test_header_tiene_precedencia_sobre_query_param(mock_get_settings) -> None:
    mock_get_settings.return_value = _settings_with_tenants("taller_a", "taller_b", "default")
    conn = _make_conn(x_tenant="taller_a", query_tenant="taller_b")
    assert resolve_tenant_key(conn) == "taller_a"
