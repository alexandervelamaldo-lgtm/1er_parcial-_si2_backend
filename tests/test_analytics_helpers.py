"""Tests unitarios de los helpers PUROS del módulo analytics.

Estos tests NO tocan DB — verifican la lógica determinística de
`geo_clustering`, `sla_policy` y `dashboard_metrics_service._normalize_tipo`.
Tests de queries SQL viven en `test_analytics_router.py` con dependency
overrides.
"""

from __future__ import annotations

import pytest

from app.services.analytics.geo_clustering import (
    GEO_PRECISION_DECIMALS,
    bucket_key,
    cluster_incidents,
)
from app.services.analytics.sla_policy import (
    SlaThresholds,
    get_sla_thresholds,
    minutes_between,
)
from app.services.analytics.dashboard_metrics_service import _normalize_tipo


# ── geo_clustering ──────────────────────────────────────────────────────


class TestGeoClustering:
    def test_bucket_key_rounds_to_3_decimals(self):
        """3 decimales de lat/lng equivalen a ~111m de celda en Santa Cruz."""
        assert bucket_key(-17.78634, -63.18221) == (-17.786, -63.182)
        # Mismo barrio → misma celda
        assert bucket_key(-17.78611, -63.18234) == bucket_key(-17.78649, -63.18211)

    def test_cluster_groups_nearby_points(self):
        # 3 puntos en la misma celda + 1 lejos → 2 zonas, la de 3 en top.
        points = [
            (-17.7860, -63.1820, "llanta"),
            (-17.7861, -63.1821, "llanta"),
            (-17.7862, -63.1822, "motor"),
            (-17.7900, -63.1900, "choque"),  # otra celda
        ]
        zones = cluster_incidents(points, top_n=10)
        assert len(zones) == 2
        # La zona de 3 puntos va primera
        assert zones[0].count == 3
        # Tipo predominante es llanta (2 vs 1)
        assert zones[0].tipo_predominante == "llanta"
        assert zones[1].count == 1

    def test_cluster_empty_returns_empty(self):
        assert cluster_incidents([]) == []

    def test_cluster_skips_invalid_coords(self):
        # None y "abc" se ignoran sin reventar
        points = [
            (None, -63.18, "motor"),
            ("abc", -63.18, "motor"),  # type: ignore[arg-type]
            (-17.78, -63.18, "motor"),
        ]
        zones = cluster_incidents(points)
        assert len(zones) == 1
        assert zones[0].count == 1

    def test_cluster_deterministic_on_tie(self):
        """Si dos tipos empatan en una celda, el alfabético gana."""
        points = [
            (-17.78, -63.18, "motor"),
            (-17.78, -63.18, "llanta"),
        ]
        zones = cluster_incidents(points)
        # llanta < motor alfabéticamente
        assert zones[0].tipo_predominante == "llanta"


# ── sla_policy ──────────────────────────────────────────────────────────


class TestSlaPolicy:
    def test_defaults_match_documented_values(self, monkeypatch):
        # Sin env vars → defaults del documento (15/45/240).
        for var in ("SLA_ASIGNACION_MIN", "SLA_LLEGADA_MIN", "SLA_CIERRE_MIN"):
            monkeypatch.delenv(var, raising=False)
        th = get_sla_thresholds()
        assert th.asignacion_min == 15
        assert th.llegada_min == 45
        assert th.cierre_min == 240

    def test_env_vars_override(self, monkeypatch):
        monkeypatch.setenv("SLA_ASIGNACION_MIN", "10")
        monkeypatch.setenv("SLA_LLEGADA_MIN", "30")
        th = get_sla_thresholds()
        assert th.asignacion_min == 10
        assert th.llegada_min == 30

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SLA_ASIGNACION_MIN", "abc")
        monkeypatch.setenv("SLA_LLEGADA_MIN", "-5")  # negativo
        th = get_sla_thresholds()
        assert th.asignacion_min == 15
        assert th.llegada_min == 45  # negativo descartado

    def test_minutes_between_handles_none(self):
        from datetime import datetime, timezone
        a = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        b = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)
        assert minutes_between(a, b) == 30.0
        assert minutes_between(None, b) is None
        assert minutes_between(a, None) is None
        # Negativo se trata como None (datos corruptos).
        assert minutes_between(b, a) is None

    def test_thresholds_as_dict_serializable(self):
        th = SlaThresholds(15, 45, 240)
        d = th.as_dict()
        assert d == {
            "sla_asignacion_min": 15,
            "sla_llegada_min": 45,
            "sla_cierre_min": 240,
        }


# ── _normalize_tipo (router de label canónico) ──────────────────────────


class TestNormalizeTipo:
    @pytest.mark.parametrize("entrada,esperado", [
        ("Batería descargada", "bateria"),
        ("Bateria sin energía", "bateria"),
        ("Llanta pinchada", "llanta"),
        ("neumático ponchado", "llanta"),
        ("pinchazo en rueda", "llanta"),
        ("Motor sobrecalentado", "motor"),
        ("Falla mecánica menor", "motor"),
        ("Choque frontal", "choque"),
        ("colisión múltiple", "choque"),
        ("Accidente leve", "choque"),
        ("Otra cosa rara", "otros"),
        (None, "otros"),
        ("", "otros"),
    ])
    def test_canonical_mapping(self, entrada, esperado):
        assert _normalize_tipo(entrada) == esperado
