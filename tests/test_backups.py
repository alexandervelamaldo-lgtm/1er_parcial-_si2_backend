"""Tests del módulo de respaldos (servicio puro + router /backups).

Sin PostgreSQL ni filesystem real de producción:
  - La lógica pura (parseo de URL, validación de nombres anti-traversal,
    schedule round-trip, is_due, retención, tamaño legible) se prueba
    redirigiendo `_backups_root` a un tmp_path.
  - El router se prueba con `dependency_overrides` para `get_current_user`
    y monkeypatcheando las funciones de `backup_service`, de modo que NUNCA
    se invoca pg_dump/pg_restore ni se toca disco de verdad.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies.auth import get_current_user
from app.routers.gestion_operativa_web import backups as backups_router
from app.services.gestion_operativa_web import backup_service
from app.services.gestion_operativa_web.backup_service import (
    BackupError,
    BackupNotFound,
    PgToolsUnavailable,
)


# ── Lógica pura ───────────────────────────────────────────────────────────


class TestParsePgUrl:
    def test_parsea_componentes_y_decodifica_clave(self):
        info = backup_service._parse_pg_url(
            "postgresql+asyncpg://user:p%40ss@db.host:5433/mydb"
        )
        assert info["host"] == "db.host"
        assert info["port"] == 5433
        assert info["user"] == "user"
        assert info["password"] == "p@ss"  # %40 → @
        assert info["dbname"] == "mydb"

    def test_valores_por_defecto_cuando_faltan(self):
        info = backup_service._parse_pg_url("postgresql://host/db")
        assert info["host"] == "host"
        assert info["port"] == 5432
        assert info["user"] == "postgres"
        assert info["password"] == ""
        assert info["dbname"] == "db"


class TestSafeNames:
    def test_safe_tenant_normaliza_y_acepta(self):
        assert backup_service._safe_tenant("  Default ") == "default"
        assert backup_service._safe_tenant("taller_01") == "taller_01"

    @pytest.mark.parametrize("bad", ["../etc", "taller-1", "ten ant", "a/b", ""])
    def test_safe_tenant_rechaza_invalidos(self, bad):
        with pytest.raises(BackupError):
            backup_service._safe_tenant(bad)

    @pytest.mark.parametrize(
        "bad",
        ["../x.dump", "evil.sh", "a/b.dump", "no_ext", "..dump"],
    )
    def test_safe_backup_name_rechaza_traversal_y_extension(self, bad):
        with pytest.raises(BackupNotFound):
            backup_service._safe_backup_name(bad)

    def test_safe_backup_name_acepta_valido(self):
        assert backup_service._safe_backup_name("default_20260101-000000_manual.dump") == (
            "default_20260101-000000_manual.dump"
        )


class TestResolveBackupPath:
    def test_archivo_inexistente_es_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_service, "_backups_root", lambda: tmp_path)
        with pytest.raises(BackupNotFound):
            backup_service.resolve_backup_path("default", "default_20260101-000000_manual.dump")

    def test_archivo_existente_devuelve_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_service, "_backups_root", lambda: tmp_path)
        d = backup_service._tenant_dir("default")
        f = d / "default_20260101-000000_manual.dump"
        f.write_bytes(b"data")
        resolved = backup_service.resolve_backup_path("default", f.name)
        assert resolved == f.resolve()


class TestHumanSize:
    @pytest.mark.parametrize(
        "num,expected",
        [(0, "0 B"), (512, "512 B"), (1024, "1.0 KB"), (1536, "1.5 KB"), (1048576, "1.0 MB")],
    )
    def test_formato(self, num, expected):
        assert backup_service.human_size(num) == expected


class TestMetaKind:
    def test_auto_vs_manual_y_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_service, "_backups_root", lambda: tmp_path)
        d = backup_service._tenant_dir("default")
        auto = d / "default_20260102-030405_auto.dump"
        auto.write_bytes(b"x")
        manual = d / "default_20260102-030405_manual.dump"
        manual.write_bytes(b"x")
        assert backup_service._meta(auto)["kind"] == "auto"
        assert backup_service._meta(manual)["kind"] == "manual"
        created = backup_service._meta(auto)["created_at"]
        assert created == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class TestScheduleRoundTrip:
    def test_save_y_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_service, "_backups_root", lambda: tmp_path)
        saved = backup_service.save_schedule(
            "default", enabled=True, frequency="weekly", hour=5, retention=10
        )
        assert saved["enabled"] is True
        assert saved["frequency"] == "weekly"
        assert saved["hour"] == 5
        assert saved["retention"] == 10
        assert saved["last_run"] is None
        # Persistió en disco y se relee igual.
        loaded = backup_service.load_schedule("default")
        assert loaded == saved
        assert backup_service._schedule_file().exists()

    def test_valores_invalidos_se_saturan(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_service, "_backups_root", lambda: tmp_path)
        saved = backup_service.save_schedule(
            "default", enabled=True, frequency="quarterly", hour=99, retention=999
        )
        assert saved["frequency"] == "daily"   # desconocida → daily
        assert saved["hour"] == 23             # clamp 0-23
        assert saved["retention"] == 50        # clamp 1-50

    def test_load_default_para_tenant_sin_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_service, "_backups_root", lambda: tmp_path)
        cfg = backup_service.load_schedule("default")
        assert cfg["enabled"] is False
        assert cfg["frequency"] == "daily"
        assert cfg["last_run"] is None


class TestIsDue:
    def _now(self):
        return datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)

    def test_deshabilitado_nunca_vence(self):
        cfg = {"enabled": False, "frequency": "hourly", "last_run": None}
        assert backup_service.is_due(cfg, self._now()) is False

    def test_primer_run_cuando_no_hay_last_run(self):
        cfg = {"enabled": True, "frequency": "daily", "hour": 2, "last_run": None}
        assert backup_service.is_due(cfg, self._now()) is True

    def test_hourly(self):
        now = self._now()
        reciente = {"enabled": True, "frequency": "hourly", "last_run": (now - timedelta(minutes=30)).isoformat()}
        viejo = {"enabled": True, "frequency": "hourly", "last_run": (now - timedelta(hours=2)).isoformat()}
        assert backup_service.is_due(reciente, now) is False
        assert backup_service.is_due(viejo, now) is True

    def test_weekly(self):
        now = self._now()
        reciente = {"enabled": True, "frequency": "weekly", "last_run": (now - timedelta(days=3)).isoformat()}
        viejo = {"enabled": True, "frequency": "weekly", "last_run": (now - timedelta(days=8)).isoformat()}
        assert backup_service.is_due(reciente, now) is False
        assert backup_service.is_due(viejo, now) is True

    def test_daily(self):
        now = self._now()  # 10:00
        ayer = (now - timedelta(days=1)).isoformat()
        due = {"enabled": True, "frequency": "daily", "hour": 2, "last_run": ayer}
        antes_de_hora = {"enabled": True, "frequency": "daily", "hour": 23, "last_run": ayer}
        hoy = {"enabled": True, "frequency": "daily", "hour": 2, "last_run": now.isoformat()}
        assert backup_service.is_due(due, now) is True          # ayer + ya pasó la hora 2
        assert backup_service.is_due(antes_de_hora, now) is False  # aún no llega la hora 23
        assert backup_service.is_due(hoy, now) is False         # ya corrió hoy


class TestPruneRetention:
    def test_conserva_manuales_y_poda_autos_viejos(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_service, "_backups_root", lambda: tmp_path)
        d = backup_service._tenant_dir("default")
        # 4 autos con fechas crecientes + 1 manual.
        for day in (1, 2, 3, 4):
            (d / f"default_2026010{day}-000000_auto.dump").write_bytes(b"x")
        (d / "default_20260101-120000_manual.dump").write_bytes(b"x")

        removed = backup_service.prune_retention("default", 2)
        assert removed == 2  # se borran los 2 autos más viejos

        remaining = {p.name for p in d.glob("*.dump")}
        assert "default_20260101-120000_manual.dump" in remaining  # manual intacto
        assert "default_20260104-000000_auto.dump" in remaining    # auto más nuevo
        assert "default_20260103-000000_auto.dump" in remaining
        assert "default_20260101-000000_auto.dump" not in remaining  # podado
        assert len(remaining) == 3


class TestNextRunIso:
    def test_none_si_deshabilitado(self):
        cfg = {"enabled": False, "frequency": "daily", "last_run": None}
        assert backup_service.next_run_iso(cfg, datetime.now(timezone.utc)) is None

    def test_valor_si_habilitado(self):
        cfg = {"enabled": True, "frequency": "daily", "hour": 2, "last_run": None}
        assert backup_service.next_run_iso(cfg, datetime.now(timezone.utc)) is not None


# ── Router /backups ───────────────────────────────────────────────────────


def _fake_user(roles: list[str], user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        email="admin@example.com",
        roles=[SimpleNamespace(name=r) for r in roles],
    )


def _make_app(user) -> FastAPI:
    app = FastAPI()
    app.include_router(backups_router.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def _meta(name: str, *, kind: str = "manual", size: int = 2048) -> dict:
    return {
        "name": name,
        "size_bytes": size,
        "size_human": backup_service.human_size(size),
        "created_at": datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc),
        "kind": kind,
    }


class TestBackupRouterAuth:
    def test_cliente_recibe_403(self):
        app = _make_app(_fake_user(["CLIENTE"]))
        resp = TestClient(app).get("/backups")
        assert resp.status_code == 403

    def test_admin_lista_200(self, monkeypatch):
        monkeypatch.setattr(
            backup_service, "list_backups",
            lambda tenant: [_meta("default_20260605-120000_manual.dump")],
        )
        monkeypatch.setattr(backup_service, "pg_tools_available", lambda: True)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).get("/backups")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["pg_available"] is True
        assert data["items"][0]["kind"] == "manual"
        assert data["items"][0]["size_human"] == "2.0 KB"

    def test_lista_refleja_pg_no_disponible(self, monkeypatch):
        monkeypatch.setattr(backup_service, "list_backups", lambda tenant: [])
        monkeypatch.setattr(backup_service, "pg_tools_available", lambda: False)
        app = _make_app(_fake_user(["OPERADOR"]))
        resp = TestClient(app).get("/backups")
        assert resp.status_code == 200
        assert resp.json()["pg_available"] is False


class TestBackupRouterCreate:
    def test_crea_201(self, monkeypatch):
        async def _fake_create(tenant, *, kind="manual"):
            assert kind == "manual"
            return _meta("default_20260605-120000_manual.dump")

        monkeypatch.setattr(backup_service, "create_backup", _fake_create)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).post("/backups")
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "default_20260605-120000_manual.dump"

    def test_pg_no_disponible_es_503(self, monkeypatch):
        async def _boom(tenant, *, kind="manual"):
            raise PgToolsUnavailable("pg_dump no está disponible")

        monkeypatch.setattr(backup_service, "create_backup", _boom)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).post("/backups")
        assert resp.status_code == 503

    def test_fallo_generico_es_500(self, monkeypatch):
        async def _boom(tenant, *, kind="manual"):
            raise BackupError("algo falló")

        monkeypatch.setattr(backup_service, "create_backup", _boom)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).post("/backups")
        assert resp.status_code == 500


class TestBackupRouterSchedule:
    def test_get_schedule_200(self, monkeypatch):
        cfg = {"enabled": True, "frequency": "daily", "hour": 3, "retention": 5, "last_run": None}
        monkeypatch.setattr(backup_service, "load_schedule", lambda tenant: cfg)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).get("/backups/schedule")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["enabled"] is True
        assert data["frequency"] == "daily"
        assert data["hour"] == 3
        assert data["next_run"] is not None  # habilitado → estimación presente

    def test_put_schedule_persiste_y_devuelve(self, monkeypatch):
        captured = {}

        def _fake_save(tenant, *, enabled, frequency, hour, retention):
            captured.update(enabled=enabled, frequency=frequency, hour=hour, retention=retention)
            return {
                "enabled": enabled, "frequency": frequency, "hour": hour,
                "retention": retention, "last_run": None,
            }

        monkeypatch.setattr(backup_service, "save_schedule", _fake_save)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        body = {"enabled": True, "frequency": "weekly", "hour": 4, "retention": 9}
        resp = TestClient(app).put("/backups/schedule", json=body)
        assert resp.status_code == 200, resp.text
        assert captured == body
        assert resp.json()["frequency"] == "weekly"

    def test_put_schedule_valida_rango(self):
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        # hour fuera de rango → 422 de pydantic.
        resp = TestClient(app).put(
            "/backups/schedule",
            json={"enabled": True, "frequency": "daily", "hour": 99, "retention": 5},
        )
        assert resp.status_code == 422


class TestBackupRouterDeleteRestore:
    def test_delete_200(self, monkeypatch):
        monkeypatch.setattr(backup_service, "delete_backup", lambda tenant, name: None)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).delete("/backups/default_20260605-120000_manual.dump")
        assert resp.status_code == 200, resp.text
        assert "eliminado" in resp.json()["detail"]

    def test_delete_inexistente_404(self, monkeypatch):
        def _boom(tenant, name):
            raise BackupNotFound(name)

        monkeypatch.setattr(backup_service, "delete_backup", _boom)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).delete("/backups/whatever.dump")
        assert resp.status_code == 404

    def test_restore_200(self, monkeypatch):
        async def _fake_restore(tenant, name):
            return None

        monkeypatch.setattr(backup_service, "restore_backup", _fake_restore)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).post("/backups/default_20260605-120000_manual.dump/restore")
        assert resp.status_code == 200, resp.text
        assert "restaurado" in resp.json()["detail"]

    def test_restore_falla_es_400(self, monkeypatch):
        async def _boom(tenant, name):
            raise BackupError("dump incompatible")

        monkeypatch.setattr(backup_service, "restore_backup", _boom)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).post("/backups/x.dump/restore")
        assert resp.status_code == 400

    def test_download_devuelve_archivo(self, tmp_path, monkeypatch):
        f = tmp_path / "default_20260605-120000_manual.dump"
        f.write_bytes(b"PGDMP-fake-bytes")
        monkeypatch.setattr(backup_service, "resolve_backup_path", lambda tenant, name: f)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).get("/backups/default_20260605-120000_manual.dump/download")
        assert resp.status_code == 200, resp.text
        assert resp.content == b"PGDMP-fake-bytes"

    def test_download_inexistente_404(self, monkeypatch):
        def _boom(tenant, name):
            raise BackupNotFound(name)

        monkeypatch.setattr(backup_service, "resolve_backup_path", _boom)
        app = _make_app(_fake_user(["ADMINISTRADOR"]))
        resp = TestClient(app).get("/backups/missing.dump/download")
        assert resp.status_code == 404
