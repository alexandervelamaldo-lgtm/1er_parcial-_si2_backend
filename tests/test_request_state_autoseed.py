from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.estados_solicitud import EstadoSolicitud
from app.routers.gestion_solicitudes import solicitudes as solicitudes_router


class _FakeSession:
    def __init__(self, existing: EstadoSolicitud | None = None) -> None:
        self._existing = existing
        self.added: list[EstadoSolicitud] = []
        self.flushed = False

    async def scalar(self, _query: object) -> EstadoSolicitud | None:
        return self._existing

    async def execute(self, _query: object):
        names = [obj.nombre for obj in self.added]

        class _Scalars:
            def __init__(self, values: list[str]) -> None:
                self._values = values

            def all(self) -> list[str]:
                return self._values

        class _Result:
            def __init__(self, values: list[str]) -> None:
                self._values = values

            def scalars(self) -> _Scalars:
                return _Scalars(self._values)

        return _Result(names)

    def add(self, obj: EstadoSolicitud) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_known_missing_request_state_is_recreated() -> None:
    session = _FakeSession()

    estado = await solicitudes_router._get_estado_por_nombre(session, "REGISTRADA")

    assert estado.nombre == "REGISTRADA"
    assert session.flushed is True
    assert session.added and session.added[0].nombre == "REGISTRADA"


@pytest.mark.asyncio
async def test_unknown_missing_request_state_still_fails() -> None:
    session = _FakeSession()

    with pytest.raises(HTTPException) as exc:
        await solicitudes_router._get_estado_por_nombre(session, "ESTADO_FANTASMA")

    assert exc.value.status_code == 404
    assert not session.added


@pytest.mark.asyncio
async def test_missing_catalog_states_are_seeded_before_listing() -> None:
    session = _FakeSession()

    await solicitudes_router._ensure_known_request_states(session)

    added_names = {obj.nombre for obj in session.added}
    assert "REGISTRADA" in added_names
    assert "EN_CAMINO" in added_names
    assert "EN_ATENCION" in added_names
    assert session.flushed is True
