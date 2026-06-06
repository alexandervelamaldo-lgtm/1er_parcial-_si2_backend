"""Unit tests (puros, sin DB real) para la idempotencia persistida de /sync/lote.

Siguiendo la convención del proyecto: la AsyncSession se mockea con AsyncMock;
verificamos la lógica de los helpers, no una base de datos real.

Lo que cubrimos:
  - `_idem_lookup`: devuelve el resultado parseado, None si no existe, y {} si
    el JSON guardado está corrupto (NUNCA debe re-ejecutar el handler).
  - `_idem_save`: True al guardar; False + rollback ante violación de unicidad
    (duplicado concurrente entre procesos/réplicas).
  - `_purge_old_idempotencia`: commitea en el happy path y se traga errores
    (la purga jamás debe tumbar la sincronización).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.routers.sync import lote as lote_module


def _mock_db() -> AsyncMock:
    """AsyncSession mock: métodos async por defecto; `add` es síncrono."""
    db = AsyncMock()
    db.add = MagicMock()  # AsyncSession.add NO es awaitable
    return db


@pytest.mark.asyncio
async def test_idem_lookup_devuelve_resultado_parseado() -> None:
    db = _mock_db()
    db.scalar.return_value = json.dumps({"solicitud_id": 7, "estado": "PENDIENTE"})

    result = await lote_module._idem_lookup(db, "key-existente")

    assert result == {"solicitud_id": 7, "estado": "PENDIENTE"}


@pytest.mark.asyncio
async def test_idem_lookup_devuelve_none_si_no_existe() -> None:
    db = _mock_db()
    db.scalar.return_value = None

    assert await lote_module._idem_lookup(db, "inexistente") is None


@pytest.mark.asyncio
async def test_idem_lookup_json_corrupto_no_revienta() -> None:
    db = _mock_db()
    db.scalar.return_value = "{json: invalido"

    # Un registro corrupto se trata como ya-procesado con payload vacío, así
    # NUNCA se vuelve a ejecutar el handler con efectos secundarios.
    assert await lote_module._idem_lookup(db, "corrupto") == {}


@pytest.mark.asyncio
async def test_idem_save_true_en_exito() -> None:
    db = _mock_db()

    ok = await lote_module._idem_save(
        db, "key-1", "crear_solicitud", 42, {"solicitud_id": 1}
    )

    assert ok is True
    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_idem_save_false_y_rollback_ante_unicidad() -> None:
    db = _mock_db()
    db.commit.side_effect = IntegrityError("INSERT", {}, Exception("unique violation"))

    ok = await lote_module._idem_save(
        db, "dup-key", "crear_solicitud", 42, {"x": 1}
    )

    # Duplicado concurrente: otra request guardó la clave primero.
    assert ok is False
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_commitea_en_happy_path() -> None:
    db = _mock_db()

    await lote_module._purge_old_idempotencia(db)

    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_se_traga_errores() -> None:
    db = _mock_db()
    db.execute.side_effect = RuntimeError("boom")

    # No debe propagar: la purga es best-effort.
    await lote_module._purge_old_idempotencia(db)

    db.rollback.assert_awaited_once()


# ── Regresión: el 500 greenlet_spawn en /sync/lote ────────────────────────────
#
# Dos bugs se combinaban:
#   1. El handler buscaba el estado "PENDIENTE", que NO existe (el inicial es
#      "REGISTRADA") → toda op crear_solicitud fallaba.
#   2. Ese fallo dispara `await db.rollback()`, que EXPIRA todos los objetos de
#      la sesión (incluido current_user, sin importar expire_on_commit). Si el
#      endpoint volvía a leer current_user.id (en el logging/_idem_save), en
#      producción saltaba un lazy-load sync → MissingGreenlet (HTTP 500).
#
# El fix captura `actor_id: int = current_user.id` UNA vez al inicio y usa ese
# int en todo el loop. Este test reproduce ambas condiciones con mocks.


class _FakeRole:
    def __init__(self, name: str) -> None:
        self.name = name


class _OneShotUser:
    """`current_user` falso cuyo `.id` solo puede leerse UNA vez.

    Simula el atributo expirado tras `db.rollback()`: un segundo acceso es lo
    que en producción reventaba con MissingGreenlet. Aquí, en su lugar, hace
    fallar el test — convirtiendo la regresión en un error determinista.
    """

    def __init__(self, uid: int, roles: list[str]) -> None:
        self._uid = uid
        self._id_reads = 0
        self.roles = [_FakeRole(r) for r in roles]

    @property
    def id(self) -> int:
        self._id_reads += 1
        if self._id_reads > 1:
            raise AssertionError(
                "current_user.id se leyó más de una vez tras un rollback "
                "(riesgo MissingGreenlet). Usa el actor_id capturado al inicio."
            )
        return self._uid


@pytest.mark.asyncio
async def test_sync_lote_op_fallida_no_revienta_por_atributo_expirado() -> None:
    db = _mock_db()
    db.info = {"tenant_key": "test"}
    # Todo scalar → None: _idem_lookup devuelve None (procesa la op) y
    # _get_estado("REGISTRADA") devuelve None → el handler lanza ValueError y se
    # ejecuta el rollback que expira current_user.
    db.scalar.return_value = None

    op = lote_module.SyncOperation(
        tipo="crear_solicitud",
        idempotency_key="k-err-1",
        payload={
            "vehiculo_id": 1,
            "tipo_incidente_id": 1,
            "latitud_incidente": -17.7,
            "longitud_incidente": -63.1,
            "descripcion": "x",
        },
        offline_created_at=None,  # sin ventana → se salta la dedup difusa
    )
    body = lote_module.SyncLoteRequest(operations=[op])
    user = _OneShotUser(7, ["CLIENTE"])

    # No debe propagar (sin 500): la op se reporta como error en la respuesta.
    resp = await lote_module.sync_lote(
        body, current_user=user, current_cliente_id=42, db=db
    )

    assert resp.total == 1
    assert resp.ok == 0
    assert resp.errors == 1
    assert resp.results[0].status == "error"
    # Confirma que se buscó el estado correcto (REGISTRADA, no PENDIENTE).
    assert "REGISTRADA" in (resp.results[0].error or "")
    db.rollback.assert_awaited()  # la op fallida hizo rollback


# ── Resolución robusta del tipo de incidente en /sync/lote ────────────────────
#
# Una emergencia creada 100% offline con el catálogo embebido del móvil puede
# traer un tipo_incidente_id que NO existe en este tenant (dos seeders distintos
# + SERIAL que no se reinicia). `_resolver_tipo_incidente` debe resolver en
# cascada (id → nombre → primer tipo) para que el FK siempre sea válido y la
# emergencia jamás se pierda al sincronizar.


class _FakeTipo:
    def __init__(self, id_: int, nombre: str) -> None:
        self.id = id_
        self.nombre = nombre


def _execute_result(value):
    """Mockea el retorno de `await db.execute(...)`: un objeto cuyo
    `.scalar_one_or_none()` (síncrono) devuelve `value`."""
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


@pytest.mark.asyncio
async def test_resolver_tipo_por_id_directo() -> None:
    db = _mock_db()
    tipo = _FakeTipo(3, "Batería")
    db.get.return_value = tipo  # el id existe en el tenant

    result = await lote_module._resolver_tipo_incidente(
        db, {"tipo_incidente_id": 3, "tipo_incidente_nombre": "Batería"}
    )

    assert result is tipo
    db.execute.assert_not_awaited()  # no hizo falta resolver por nombre/primero


@pytest.mark.asyncio
async def test_resolver_tipo_por_nombre_si_id_no_existe() -> None:
    db = _mock_db()
    db.get.return_value = None  # id "adivinado" por el catálogo offline embebido
    tipo = _FakeTipo(9, "Choque")
    db.execute.return_value = _execute_result(tipo)

    result = await lote_module._resolver_tipo_incidente(
        db, {"tipo_incidente_id": 1, "tipo_incidente_nombre": "Choque"}
    )

    assert result is tipo
    db.execute.assert_awaited_once()  # resolvió por nombre, no llegó al "primero"


@pytest.mark.asyncio
async def test_resolver_tipo_primer_disponible_si_nombre_no_matchea() -> None:
    db = _mock_db()
    db.get.return_value = None
    primero = _FakeTipo(1, "Llanta ponchada")
    # 1ª execute (por nombre) → None; 2ª execute (primer tipo) → primero
    db.execute.side_effect = [_execute_result(None), _execute_result(primero)]

    result = await lote_module._resolver_tipo_incidente(
        db, {"tipo_incidente_id": 99, "tipo_incidente_nombre": "Inexistente"}
    )

    assert result is primero
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_resolver_tipo_sin_nombre_va_directo_al_primero() -> None:
    db = _mock_db()
    db.get.return_value = None
    primero = _FakeTipo(5, "Combustible")
    db.execute.return_value = _execute_result(primero)

    # payload sin 'tipo_incidente_nombre' → se salta la consulta por nombre.
    result = await lote_module._resolver_tipo_incidente(db, {"tipo_incidente_id": 7})

    assert result is primero
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolver_tipo_none_si_tenant_sin_tipos() -> None:
    db = _mock_db()
    db.get.return_value = None
    db.execute.side_effect = [_execute_result(None), _execute_result(None)]

    result = await lote_module._resolver_tipo_incidente(
        db, {"tipo_incidente_id": 1, "tipo_incidente_nombre": "Choque"}
    )

    # Tenant sin ningún tipo configurado → None (el handler lo convierte en
    # ValueError, no en un FK roto).
    assert result is None
