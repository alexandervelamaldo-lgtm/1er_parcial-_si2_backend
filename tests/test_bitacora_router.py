"""Tests del router /bitacora.

Sin PostgreSQL: usamos `dependency_overrides` para reemplazar `get_db` por
una sesión fake que devuelve filas canned, y `get_current_user` por usuarios
de cada rol. Verifican:

  - Auth: 403 si el rol no es administrativo/operativo, 200 si lo es.
  - Validación: 400 si since > until.
  - Forma del payload: items + total + paginación.
  - El describe_action (función pura) traduce rutas a acciones legibles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies.auth import get_current_user
from app.database import get_db
from app.routers.gestion_operativa_web import bitacora as bitacora_router
from app.services.gestion_operativa_web.bitacora_service import describe_action


def _fake_user(roles: list[str], user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        email="admin@example.com",
        roles=[SimpleNamespace(name=r) for r in roles],
    )


def _fake_row():
    """Una fila (Bitacora-like, email) como la devuelve el outer join."""
    return (
        SimpleNamespace(
            id=7,
            created_at=datetime.now(timezone.utc),
            user_id=2,
            accion="Cambió el estado de una solicitud",
            metodo="PUT",
            ruta="/solicitudes/12/estado",
            status_code=200,
            entidad="solicitud",
            entidad_id="12",
            ip="127.0.0.1",
        ),
        "operador@example.com",
    )


def _make_app(user, *, rows=None, total=0) -> FastAPI:
    app = FastAPI()
    app.include_router(bitacora_router.router)

    count_result = MagicMock()
    count_result.scalar.return_value = total
    rows_result = MagicMock()
    rows_result.all.return_value = rows or []

    fake_db = MagicMock()
    fake_db.info = {"tenant_key": "default"}
    # 1ª execute → count; 2ª execute → items.
    fake_db.execute = AsyncMock(side_effect=[count_result, rows_result])

    async def _get_db_override():
        yield fake_db

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = lambda: user
    return app


class TestBitacoraAuth:
    def test_cliente_recibe_403(self):
        app = _make_app(_fake_user(["CLIENTE"]))
        resp = TestClient(app).get("/bitacora")
        assert resp.status_code == 403

    def test_administrador_recibe_200(self):
        app = _make_app(_fake_user(["ADMINISTRADOR"]), rows=[_fake_row()], total=1)
        resp = TestClient(app).get("/bitacora")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["limit"] == 50 and data["offset"] == 0
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["accion"] == "Cambió el estado de una solicitud"
        assert item["entidad"] == "solicitud"
        assert item["entidad_id"] == "12"
        assert item["user_email"] == "operador@example.com"

    def test_operador_recibe_200_vacio(self):
        app = _make_app(_fake_user(["OPERADOR"]), rows=[], total=0)
        resp = TestClient(app).get("/bitacora")
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []


class TestBitacoraValidacion:
    def test_since_mayor_que_until_devuelve_400(self):
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).get("/bitacora?since=2026-12-31&until=2026-01-01")
        assert resp.status_code == 400


class TestDescribeAction:
    @pytest.mark.parametrize(
        "metodo,ruta,accion,entidad,entidad_id",
        [
            ("POST", "/solicitudes", "Creó una solicitud", "solicitud", None),
            ("PUT", "/solicitudes/12/estado", "Cambió el estado de una solicitud", "solicitud", "12"),
            ("PUT", "/solicitudes/5/asignar", "Asignó una solicitud a un taller", "solicitud", "5"),
            ("POST", "/solicitudes/9/pago", "Registró un pago", "pago", "9"),
            ("PUT", "/talleres/3", "Actualizó un taller", "taller", "3"),
        ],
    )
    def test_describe_action_mapea_rutas(self, metodo, ruta, accion, entidad, entidad_id):
        a, e, eid = describe_action(metodo, ruta)
        assert a == accion
        assert e == entidad
        assert eid == entidad_id

    def test_describe_action_fallback_generico(self):
        a, e, eid = describe_action("DELETE", "/cosa/42/sub")
        assert a == "Eliminó un registro"
        assert e == "cosa"
        assert eid == "42"
