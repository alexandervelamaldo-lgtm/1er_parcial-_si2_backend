from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.routers.gestion_operativa_web import kpis as kpis_router
from app.schemas.gestion_operativa_web.kpis import KpisResumenResponse


def _fake_user(roles: list[str], user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        email=f"user{user_id}@example.com",
        roles=[SimpleNamespace(name=role) for role in roles],
    )


def _make_app_with_user(user, tenant_state: dict[str, str]) -> tuple[FastAPI, MagicMock]:
    app = FastAPI()
    app.include_router(kpis_router.router)

    fake_db = MagicMock()
    fake_db.info = {"tenant_key": tenant_state["value"]}
    fake_db.execute = AsyncMock()

    async def _get_db_override():
        fake_db.info = {"tenant_key": tenant_state["value"]}
        yield fake_db

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = lambda: user
    return app, fake_db


def _sample_kpis(total: int) -> KpisResumenResponse:
    return KpisResumenResponse(
        tiempo_asignacion_promedio_min=5.5,
        tiempo_llegada_promedio_min=9.2,
        tiempo_atencion_promedio_min=20.0,
        total_solicitudes=total,
        solicitudes_activas=max(total - 1, 0),
        solicitudes_completadas=1 if total else 0,
        solicitudes_canceladas=0,
        tasa_completados=1 / total if total else 0.0,
        tasa_cancelacion=0.0,
        incidentes_por_tipo={"Grua": total},
        zonas_top=[],
        solicitudes_por_dia=[],
        talleres=[],
        calculado_en="2026-06-07T12:00:00+00:00",
        cache_ttl_segundos=900,
    )


class TestKpisRouterMultiTenant:
    def setup_method(self):
        kpis_router._cache.clear()

    def test_cache_segmenta_por_tenant(self):
        tenant_state = {"value": "tenant_a"}
        app, _ = _make_app_with_user(_fake_user(["OPERADOR"]), tenant_state)
        client = TestClient(app)

        with patch.object(
            kpis_router,
            "_compute_full_kpis",
            new=AsyncMock(side_effect=[_sample_kpis(3), _sample_kpis(7)]),
        ) as compute:
            resp_a = client.get("/kpis/resumen")
            tenant_state["value"] = "tenant_b"
            resp_b = client.get("/kpis/resumen")
            tenant_state["value"] = "tenant_a"
            resp_a_cached = client.get("/kpis/resumen")

        assert resp_a.status_code == 200, resp_a.text
        assert resp_b.status_code == 200, resp_b.text
        assert resp_a_cached.status_code == 200, resp_a_cached.text
        assert resp_a.json()["total_solicitudes"] == 3
        assert resp_b.json()["total_solicitudes"] == 7
        assert resp_a_cached.json()["total_solicitudes"] == 3
        assert compute.await_count == 2

    def test_cache_segmenta_por_usuario_taller(self):
        tenant_state = {"value": "tenant_demo"}
        app_user_a, _ = _make_app_with_user(_fake_user(["TALLER"], user_id=101), tenant_state)
        app_user_b, _ = _make_app_with_user(_fake_user(["TALLER"], user_id=202), tenant_state)
        client_a = TestClient(app_user_a)
        client_b = TestClient(app_user_b)

        with patch.object(
            kpis_router,
            "_compute_full_kpis",
            new=AsyncMock(side_effect=[_sample_kpis(2), _sample_kpis(5)]),
        ) as compute:
            resp_a = client_a.get("/kpis/resumen")
            resp_b = client_b.get("/kpis/resumen")
            resp_a_cached = client_a.get("/kpis/resumen")

        assert resp_a.status_code == 200, resp_a.text
        assert resp_b.status_code == 200, resp_b.text
        assert resp_a_cached.status_code == 200, resp_a_cached.text
        assert resp_a.json()["total_solicitudes"] == 2
        assert resp_b.json()["total_solicitudes"] == 5
        assert resp_a_cached.json()["total_solicitudes"] == 2
        assert compute.await_count == 2

    def test_taller_no_puede_ver_kpi_de_otro_taller(self):
        tenant_state = {"value": "tenant_demo"}
        user = _fake_user(["TALLER"], user_id=77)
        app, fake_db = _make_app_with_user(user, tenant_state)
        client = TestClient(app)

        fake_db.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(id=10, nombre="Mi taller"),
            )
        )

        response = client.get("/kpis/taller/999")

        assert response.status_code == 403
        assert "propio taller" in response.json()["detail"]
