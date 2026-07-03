from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.gestion_solicitudes import solicitudes as solicitudes_router


def _user(*roles: str) -> SimpleNamespace:
    return SimpleNamespace(roles=[SimpleNamespace(name=role) for role in roles])


def _solicitud(*, cliente_id: int = 10, tecnico_id: int | None = None, taller_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(cliente_id=cliente_id, tecnico_id=tecnico_id, taller_id=taller_id)


def test_admin_tenant_can_open_request_detail_in_current_tenant() -> None:
    solicitudes_router.validate_request_access(
        _user("ADMIN_TENANT"),
        current_cliente_id=None,
        current_tecnico_id=None,
        current_taller_id=None,
        solicitud=_solicitud(cliente_id=22, tecnico_id=4, taller_id=8),
    )


def test_admin_tenant_is_not_cross_tenant_privileged() -> None:
    assert solicitudes_router._has_cross_tenant_request_visibility({"ADMIN_TENANT"}) is False
    assert solicitudes_router._has_cross_tenant_request_visibility({"ADMINISTRADOR"}) is True


def test_empty_tank_requests_are_labeled_with_technical_term() -> None:
    tags = solicitudes_router._specialize_diagnostic_tags(
        tipo_incidente="Combustible",
        descripcion="El vehículo quedó sin combustible y con el tanque vacío",
        tags=["combustible"],
    )
    assert tags == ["tanque_vacio"]

    summary = solicitudes_router._build_technical_diagnostic_summary(
        tipo_incidente="Combustible",
        descripcion="El vehículo quedó sin combustible y con el tanque vacío",
        base_summary="La IA requiere validación manual por baja confianza o información insuficiente",
        requires_manual_review=True,
        tags=tags,
    )
    assert "Diagnóstico técnico: tanque vacío." in summary
    assert "Diagnóstico pendiente de validación técnica manual." in summary


def test_non_owner_workshop_stays_blocked() -> None:
    with pytest.raises(HTTPException) as exc:
        solicitudes_router.validate_request_access(
            _user("TALLER"),
            current_cliente_id=None,
            current_tecnico_id=None,
            current_taller_id=3,
            solicitud=_solicitud(cliente_id=9, tecnico_id=1, taller_id=8),
        )
    assert exc.value.status_code == 403
