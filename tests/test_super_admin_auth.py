"""Tests del login de super-admin contra la control DB.

Estrategia:
  - Usamos SQLite in-memory async para la control DB — sin levantar
    Postgres en CI. Esto es posible porque los modelos del control
    plane usan tipos portables (BigInteger, String, DateTime).
  - Para los tests del flujo de tenants, mockeamos
    `_find_user_across_tenants` con AsyncMock — no necesitamos DB de
    tenants real para verificar la precedencia super-admin > tenant.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.control_plane.database import ControlBase
from app.control_plane.models.super_admin import SuperAdmin
from app.routers.autenticacion_acceso import auth as auth_router
from app.utils.auth import hash_password


# ── Fixtures: control DB efímera por test ──────────────────────────────


@pytest_asyncio.fixture
async def control_session():
    """Una control DB SQLite in-memory creada para este test, destruida
    al finalizar. Cada test arranca con DB limpia."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(ControlBase.metadata.create_all)
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    # Patcheamos el sessionmaker global de la control DB.
    with patch.object(auth_router, "get_control_sessionmaker", return_value=sessionmaker):
        async with sessionmaker() as session:
            yield session
    await engine.dispose()


async def _seed_super_admin(session, email: str, password: str, suspended: bool = False) -> SuperAdmin:
    admin = SuperAdmin(
        email=email,
        password_hash=hash_password(password),
        suspended=suspended,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return admin


# ── T1: Login super-admin desde control DB ──────────────────────────────


@pytest.mark.asyncio
async def test_T1_super_admin_login_uses_control_db(control_session):
    """Sembrar un super-admin en control DB. Login devuelve tenant='*'."""
    await _seed_super_admin(control_session, "admin@platform.com", "Secret123*")

    admin = await auth_router._find_super_admin("admin@platform.com", "Secret123*")
    assert admin is not None
    assert admin.email == "admin@platform.com"
    # Construir el token y verificar campos clave
    response = auth_router._build_super_admin_token(admin)
    assert response.tenant_key == auth_router.SUPER_ADMIN_TENANT_MARKER
    assert response.tenant_key == "*"
    assert response.access_token  # no vacío
    # El JWT debe contener is_super_admin=True
    from app.utils.auth import decode_token
    payload = decode_token(response.access_token)
    assert payload.get("is_super_admin") is True
    assert payload.get("tenant") == "*"
    assert "SUPER_ADMIN" in payload.get("roles", [])


# ── T2: Precedencia sobre user del tenant ──────────────────────────────


@pytest.mark.asyncio
async def test_T2_super_admin_takes_precedence_over_tenant_user(control_session):
    """Mismo email en control DB y supuestamente en un tenant. El control
    DB gana — verificamos a nivel de `_find_super_admin`."""
    await _seed_super_admin(control_session, "alex@x.com", "ControlPwd*")

    # Si el password del control DB matchea, NO debemos fall-through a tenants.
    admin = await auth_router._find_super_admin("alex@x.com", "ControlPwd*")
    assert admin is not None
    # Si el password no matchea, devolvemos None — el login caería al
    # flujo de tenants (testeado en T3).
    miss = await auth_router._find_super_admin("alex@x.com", "WrongPwd")
    assert miss is None


# ── T3: Usuario regular sigue funcionando cuando control DB está vacía ─


@pytest.mark.asyncio
async def test_T3_regular_user_still_works_when_control_db_empty(control_session):
    """Control DB sin filas. _find_super_admin devuelve None — el login
    se delega al flujo normal de tenants (no testeado acá; eso está en
    los tests existentes)."""
    admin = await auth_router._find_super_admin("operador@emergency.com", "Password123*")
    assert admin is None


# ── Edge cases adicionales ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suspended_super_admin_cannot_login(control_session):
    """Un super-admin suspendido NO puede loguearse."""
    await _seed_super_admin(control_session, "suspended@x.com", "Secret123*", suspended=True)
    admin = await auth_router._find_super_admin("suspended@x.com", "Secret123*")
    assert admin is None


@pytest.mark.asyncio
async def test_super_admin_jwt_expires_shorter_than_normal(control_session):
    """T8: el JWT del super-admin expira en ≤15 min."""
    from app.config import get_settings
    settings = get_settings()
    admin = await _seed_super_admin(control_session, "expiry@x.com", "Secret123*")
    response = auth_router._build_super_admin_token(admin)
    from app.utils.auth import decode_token
    payload = decode_token(response.access_token)
    exp_ts = payload["exp"]
    now_ts = datetime.now(timezone.utc).timestamp()
    minutes_to_expire = (exp_ts - now_ts) / 60.0
    # 15 minutos por default — toleramos margen de ±2 min por la
    # latencia del test.
    expected = settings.super_admin_token_expire_minutes
    assert expected - 2 <= minutes_to_expire <= expected + 2
    # Y debe ser MÁS CORTO que el del usuario normal (30 min default).
    assert minutes_to_expire < settings.access_token_expire_minutes


@pytest.mark.asyncio
async def test_control_db_inaccessible_returns_none(monkeypatch):
    """Si la control DB explota al abrirse, _find_super_admin devuelve
    None sin propagar excepción — el login normal sigue funcionando."""
    def _boom():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(auth_router, "get_control_sessionmaker", _boom)
    result = await auth_router._find_super_admin("x@y.com", "anything")
    assert result is None
