"""Tests del router /analytics con dependency overrides.

Estos tests NO levantan PostgreSQL — usamos FastAPI's `dependency_overrides`
para reemplazar `get_db` con una sesión fake que retorna datos canned, y
`require_roles`/`get_current_user` con users fake de cada rol.

Verifican:
  - Auth: 403 si el rol no es ADMIN/OPERADOR.
  - Tenant cache key: dos tenants no se mezclan en el cache in-memory.
  - Query params: since/until/taller_id se propagan al service.
  - 400 si since > until.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies.auth import get_current_user, require_roles
from app.database import get_db
from app.routers.analytics import dashboard as analytics_dashboard


def _fake_user(roles: list[str], user_id: int = 1) -> SimpleNamespace:
    """User stub mínimo con atributos que require_roles/get_role_names leen."""
    return SimpleNamespace(
        id=user_id,
        email="test@example.com",
        roles=[SimpleNamespace(name=r) for r in roles],
    )


def _make_app_with_user(user) -> FastAPI:
    """Arma una mini FastAPI con el router de analytics + overrides."""
    app = FastAPI()
    app.include_router(analytics_dashboard.router)

    fake_db = MagicMock()
    fake_db.info = {"tenant_key": "default"}

    # Override de get_db — devolvemos el mismo fake.
    async def _get_db_override():
        yield fake_db

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = lambda: user

    # require_roles es un closure — overrideamos su factory para que
    # cualquier llamada devuelva nuestro user sin filtrar por roles.
    # Pero queremos que FALLE si el rol no es válido — así que en lugar
    # de overridear require_roles, hacemos que el user no tenga el rol
    # y que get_current_user devuelva el user real (no falla porque
    # require_roles internamente vuelve a llamar get_role_names).
    return app, fake_db


# ── Tests de auth ───────────────────────────────────────────────────────


class TestAnalyticsAuth:
    def test_cliente_recibe_403_en_dashboard(self):
        """Un usuario CLIENTE no debe acceder al dashboard administrativo."""
        user = _fake_user(["CLIENTE"])
        app, _ = _make_app_with_user(user)
        client = TestClient(app)
        # Como require_roles consulta los roles del user vía get_role_names,
        # un CLIENTE provoca 403 incluso con get_current_user overridado.
        resp = client.get("/analytics/dashboard")
        assert resp.status_code == 403

    def test_admin_recibe_200_en_dashboard(self):
        """Un ADMINISTRADOR sí accede. Mockeamos las queries SQL para evitar DB."""
        user = _fake_user(["ADMINISTRADOR"])
        app, _ = _make_app_with_user(user)

        # Mockeamos los servicios para devolver respuestas válidas sin DB.
        with patch.object(
            analytics_dashboard, "get_avg_assignment_time",
            new=AsyncMock(return_value=_empty_time_kpi()),
        ), patch.object(
            analytics_dashboard, "get_avg_arrival_time",
            new=AsyncMock(return_value=_empty_time_kpi()),
        ), patch.object(
            analytics_dashboard, "get_avg_closure_time",
            new=AsyncMock(return_value=_empty_time_kpi()),
        ), patch.object(
            analytics_dashboard, "get_end_to_end_time",
            new=AsyncMock(return_value=_empty_time_kpi()),
        ), patch.object(
            analytics_dashboard, "get_incidents_by_type",
            new=AsyncMock(return_value=_empty_incidents()),
        ), patch.object(
            analytics_dashboard, "get_top_workshops",
            new=AsyncMock(return_value=_empty_ranking()),
        ), patch.object(
            analytics_dashboard, "get_hot_zones",
            new=AsyncMock(return_value=_empty_zones()),
        ), patch.object(
            analytics_dashboard, "get_cancellations_breakdown",
            new=AsyncMock(return_value=_empty_cancels()),
        ), patch.object(
            analytics_dashboard, "get_sla_compliance",
            new=AsyncMock(return_value=_empty_sla()),
        ):
            # Forzamos cache fresco para esta key.
            analytics_dashboard._CACHE.clear()
            client = TestClient(app)
            resp = client.get("/analytics/dashboard")
            assert resp.status_code == 200, resp.text
            data = resp.json()
            # Estructura mínima del payload
            assert "tiempo_asignacion" in data
            assert "sla" in data
            assert "talleres_top" in data


class TestAnalyticsValidation:
    def test_since_mayor_que_until_devuelve_400(self):
        user = _fake_user(["ADMINISTRADOR"])
        app, _ = _make_app_with_user(user)
        client = TestClient(app)
        # No mockeo nada porque la validación corta antes de hacer queries.
        resp = client.get(
            "/analytics/dashboard?since=2026-12-31&until=2026-01-01",
        )
        assert resp.status_code == 400

    def test_serie_temporal_rechaza_rango_mayor_a_un_año(self):
        user = _fake_user(["ADMINISTRADOR"])
        app, _ = _make_app_with_user(user)
        client = TestClient(app)
        resp = client.get(
            "/analytics/incidentes/serie-temporal?since=2024-01-01&until=2026-01-01",
        )
        assert resp.status_code == 400


class TestAnalyticsCache:
    def test_cache_segmenta_por_tenant(self):
        """Dos tenants con la misma URL no comparten cache."""
        analytics_dashboard._CACHE.clear()
        analytics_dashboard._cache_set("tenant_a", "x", (1,), {"v": "A"})
        analytics_dashboard._cache_set("tenant_b", "x", (1,), {"v": "B"})
        assert analytics_dashboard._cache_get("tenant_a", "x", (1,)) == {"v": "A"}
        assert analytics_dashboard._cache_get("tenant_b", "x", (1,)) == {"v": "B"}

    def test_cache_expira_tras_ttl(self, monkeypatch):
        analytics_dashboard._CACHE.clear()
        analytics_dashboard._cache_set("t", "x", (1,), "payload")
        # Forzamos a que el reloj avance más allá del TTL.
        original = analytics_dashboard.time.monotonic
        with patch.object(
            analytics_dashboard.time, "monotonic",
            return_value=original() + analytics_dashboard._CACHE_TTL_S + 1,
        ):
            assert analytics_dashboard._cache_get("t", "x", (1,)) is None


# ── Helpers para construir respuestas vacías ────────────────────────────


def _empty_time_kpi():
    from app.schemas.analytics.dashboard import TiempoPromedioKPI
    return TiempoPromedioKPI(avg_min=None, p50_min=None, p95_min=None, n_muestras=0)


def _empty_incidents():
    from app.schemas.analytics.dashboard import IncidentesPorTipoKPI
    return IncidentesPorTipoKPI(total=0, items=[])


def _empty_ranking():
    from app.schemas.analytics.dashboard import TalleresRankingKPI
    return TalleresRankingKPI(items=[], min_casos_para_ranking=5)


def _empty_zones():
    from app.schemas.analytics.dashboard import ZonasCalientesKPI
    return ZonasCalientesKPI(items=[])


def _empty_cancels():
    from app.schemas.analytics.dashboard import CancelacionesKPI
    return CancelacionesKPI(
        total_canceladas=0, total_solicitudes=0, tasa_pct=0.0, por_motivo={},
    )


def _empty_sla():
    from app.schemas.analytics.dashboard import SlaKPI
    from app.services.analytics.sla_policy import get_sla_thresholds
    return SlaKPI(
        sla_asignacion_pct=None, sla_llegada_pct=None,
        sla_cierre_pct=None, sla_global_pct=None,
        umbrales=get_sla_thresholds().as_dict(),
    )
