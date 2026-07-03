import pytest
from pydantic import ValidationError

from app.schemas.gestion_operativa_web.talleres import TallerAdminCreate


def test_taller_admin_create_accepts_valid_coordinates() -> None:
    payload = TallerAdminCreate(
        categoria_id=1,
        nombre="Taller Norte",
        direccion="Av. Banzer 4to anillo",
        latitud=-17.7833,
        longitud=-63.1821,
        telefono="70000001",
        capacidad=4,
        servicios=["motor", "llantas"],
        email="taller.norte@example.com",
        password="Password123*",
    )

    assert payload.latitud == -17.7833
    assert payload.longitud == -63.1821


def test_taller_admin_create_rejects_invalid_latitude() -> None:
    with pytest.raises(ValidationError):
        TallerAdminCreate(
            categoria_id=1,
            nombre="Taller Norte",
            direccion="Av. Banzer 4to anillo",
            latitud=120,
            longitud=-63.1821,
            telefono="70000001",
            capacidad=4,
        )
