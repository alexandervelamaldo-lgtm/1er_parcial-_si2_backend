"""Tests del dispatcher tenant_strategy.

Verifica la lógica que decide si el sistema corre en modo
`database` (un DB por tenant) o `schema` (un schema por tenant en una
sola DB). Es importante porque toda la cadena de aislamiento depende
de que este flag se lea bien.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.tenant_strategy import (
    CONTROL_SCHEMA_NAME,
    TENANT_SCHEMA_PREFIX,
    control_schema_translate_map,
    schema_for_tenant,
    schema_translate_map_for_tenant,
    using_schema_strategy,
)


class TestStrategyDetection:
    def test_default_is_database_mode(self, monkeypatch):
        """Sin env var, el modo es `database` (compat con local actual)."""
        monkeypatch.delenv("TENANT_STRATEGY", raising=False)
        get_settings.cache_clear()
        assert using_schema_strategy() is False

    def test_schema_mode_from_env(self, monkeypatch):
        monkeypatch.setenv("TENANT_STRATEGY", "schema")
        get_settings.cache_clear()
        assert using_schema_strategy() is True

    def test_schema_mode_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("TENANT_STRATEGY", "SCHEMA")
        get_settings.cache_clear()
        assert using_schema_strategy() is True

    def test_unrecognized_value_falls_back_to_database(self, monkeypatch):
        monkeypatch.setenv("TENANT_STRATEGY", "row")
        get_settings.cache_clear()
        # Cualquier valor distinto de "schema" cae a database — comportamiento seguro.
        assert using_schema_strategy() is False


class TestSchemaNaming:
    def test_default_tenant_has_prefix(self):
        assert schema_for_tenant("default") == f"{TENANT_SCHEMA_PREFIX}default"
        assert schema_for_tenant("default").startswith(TENANT_SCHEMA_PREFIX)

    def test_lowercases_input(self):
        assert schema_for_tenant("Taller_Norte") == f"{TENANT_SCHEMA_PREFIX}taller_norte"

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            schema_for_tenant("")

    def test_rejects_invalid_characters(self):
        # Espacios y guiones rompen el nombre del schema.
        with pytest.raises(ValueError):
            schema_for_tenant("taller con espacios")
        with pytest.raises(ValueError):
            schema_for_tenant("taller-norte")


class TestTranslateMap:
    def test_translate_map_points_to_tenant_schema(self):
        m = schema_translate_map_for_tenant("default")
        # SQLAlchemy usa None como "schema implícito" → mapea al schema del tenant.
        assert m == {None: "tenant_default"}

    def test_control_translate_map_is_constant(self):
        m = control_schema_translate_map()
        assert m == {None: CONTROL_SCHEMA_NAME}
        assert CONTROL_SCHEMA_NAME == "control_plane"
