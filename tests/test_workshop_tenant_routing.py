import pytest


@pytest.mark.asyncio
async def test_resolve_workshop_tenant_key_fallback(monkeypatch):
    import app.control_plane.database as control_plane_db
    from app.services.workshop_tenant_routing import resolve_workshop_tenant_key

    def _raise():
        raise RuntimeError("no control plane")

    monkeypatch.setattr(control_plane_db, "get_control_sessionmaker", _raise)

    assert await resolve_workshop_tenant_key(tipo_incidente_nombre="Llanta pinchada") == "llaneros"
    assert await resolve_workshop_tenant_key(tipo_incidente_nombre="Carrocería golpeada") == "chapa_pintura"
    assert await resolve_workshop_tenant_key(tipo_incidente_nombre="Garantía de fábrica") == "vehiculos_nuevos_garantia"
    assert await resolve_workshop_tenant_key(tipo_incidente_nombre="Falla de motor") == "mecanica_general"

