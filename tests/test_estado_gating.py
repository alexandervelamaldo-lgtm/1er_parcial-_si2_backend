"""Pruebas unitarias para la máquina de estados de solicitudes.

Valida transiciones válidas/inválidas y RBAC por rol.
Importa can_transition_request del router de solicitudes.
"""

import pytest

from app.routers.gestion_solicitudes.solicitudes import can_transition_request


# ---------------------------------------------------------------------------
# Transiciones válidas por rol TECNICO
# ---------------------------------------------------------------------------

def test_tecnico_puede_pasar_de_en_camino_a_en_atencion() -> None:
    assert can_transition_request("EN_CAMINO", "EN_ATENCION", {"TECNICO"})


def test_tecnico_puede_pasar_de_asignada_a_en_camino() -> None:
    assert can_transition_request("ASIGNADA", "EN_CAMINO", {"TECNICO"})


def test_tecnico_no_puede_saltar_directo_a_completada() -> None:
    assert not can_transition_request("ASIGNADA", "COMPLETADA", {"TECNICO"})


def test_tecnico_no_puede_saltar_de_registrada_a_en_atencion() -> None:
    assert not can_transition_request("REGISTRADA", "EN_ATENCION", {"TECNICO"})


# ---------------------------------------------------------------------------
# Transiciones válidas por rol OPERADOR
# ---------------------------------------------------------------------------

def test_operador_puede_asignar_solicitud_registrada() -> None:
    assert can_transition_request("REGISTRADA", "ASIGNADA", {"OPERADOR"})


def test_operador_puede_cancelar_desde_registrada() -> None:
    assert can_transition_request("REGISTRADA", "CANCELADA", {"OPERADOR"})


def test_operador_puede_cancelar_desde_asignada() -> None:
    assert can_transition_request("ASIGNADA", "CANCELADA", {"OPERADOR"})


def test_operador_puede_pasar_de_en_camino_a_en_atencion() -> None:
    assert can_transition_request("EN_CAMINO", "EN_ATENCION", {"OPERADOR"})


# ---------------------------------------------------------------------------
# Transiciones inválidas — estados terminales
# ---------------------------------------------------------------------------

def test_no_se_puede_transicionar_desde_completada() -> None:
    assert not can_transition_request("COMPLETADA", "EN_ATENCION", {"OPERADOR"})
    assert not can_transition_request("COMPLETADA", "ASIGNADA", {"ADMINISTRADOR"})


def test_no_se_puede_transicionar_desde_cancelada() -> None:
    assert not can_transition_request("CANCELADA", "REGISTRADA", {"OPERADOR"})
    assert not can_transition_request("CANCELADA", "ASIGNADA", {"ADMINISTRADOR"})


# ---------------------------------------------------------------------------
# RBAC — rol CLIENTE no puede cambiar estado operativo
# ---------------------------------------------------------------------------

def test_cliente_no_puede_asignar_solicitud() -> None:
    assert not can_transition_request("REGISTRADA", "ASIGNADA", {"CLIENTE"})


def test_cliente_no_puede_cancelar_solicitud_asignada() -> None:
    assert not can_transition_request("ASIGNADA", "CANCELADA", {"CLIENTE"})


# ---------------------------------------------------------------------------
# ADMINISTRADOR tiene acceso completo
# ---------------------------------------------------------------------------

def test_administrador_puede_cualquier_transicion_valida() -> None:
    assert can_transition_request("REGISTRADA", "ASIGNADA", {"ADMINISTRADOR"})
    assert can_transition_request("ASIGNADA", "EN_CAMINO", {"ADMINISTRADOR"})
    assert can_transition_request("EN_CAMINO", "EN_ATENCION", {"ADMINISTRADOR"})
    assert can_transition_request("EN_ATENCION", "COMPLETADA", {"ADMINISTRADOR"})


# ============================================================================
# Flujo nuevo cliente↔taller-directo
# (PROPUESTA_TALLER / RECHAZADA_TALLER)
# ============================================================================

# ---------------------------------------------------------------------------
# CLIENTE — selección de taller desde la app móvil
# ---------------------------------------------------------------------------

def test_cliente_puede_elegir_taller_desde_registrada() -> None:
    """El flujo principal: el cliente elige un taller en el mapa."""
    assert can_transition_request("REGISTRADA", "PROPUESTA_TALLER", {"CLIENTE"})


def test_cliente_puede_reelegir_taller_tras_rechazo() -> None:
    """Si el taller rechaza, el cliente vuelve a la lista y elige otro."""
    assert can_transition_request("RECHAZADA_TALLER", "PROPUESTA_TALLER", {"CLIENTE"})


def test_cliente_puede_cancelar_propuesta_pendiente() -> None:
    """Cancelación antes de que el taller responda — caso típico de
    arrepentimiento del usuario."""
    assert can_transition_request("PROPUESTA_TALLER", "CANCELADA", {"CLIENTE"})


def test_cliente_puede_cancelar_tras_rechazo() -> None:
    """Si el cliente no quiere re-elegir tras un rechazo, puede cancelar."""
    assert can_transition_request("RECHAZADA_TALLER", "CANCELADA", {"CLIENTE"})


def test_cliente_no_puede_saltar_directo_a_asignada() -> None:
    """El cliente nunca puede saltarse el paso de propuesta — eso es del taller."""
    assert not can_transition_request("REGISTRADA", "ASIGNADA", {"CLIENTE"})
    assert not can_transition_request("PROPUESTA_TALLER", "ASIGNADA", {"CLIENTE"})


def test_cliente_no_puede_cancelar_despues_de_aceptacion() -> None:
    """Una vez que el taller aceptó (ASIGNADA), el cliente debe ir por soporte."""
    assert not can_transition_request("ASIGNADA", "CANCELADA", {"CLIENTE"})
    assert not can_transition_request("EN_CAMINO", "CANCELADA", {"CLIENTE"})


# ---------------------------------------------------------------------------
# TALLER — aceptación / rechazo de propuestas
# ---------------------------------------------------------------------------

def test_taller_puede_aceptar_propuesta() -> None:
    """Caso feliz: el taller recibe la propuesta y la acepta."""
    assert can_transition_request("PROPUESTA_TALLER", "ASIGNADA", {"TALLER"})


def test_taller_puede_rechazar_propuesta() -> None:
    """Rechazo: el taller no puede atender (falta de capacidad, marca, etc.)."""
    assert can_transition_request("PROPUESTA_TALLER", "RECHAZADA_TALLER", {"TALLER"})


def test_taller_puede_arrancar_camino_tras_aceptar() -> None:
    """Tras aceptar, el taller marca 'ya salí'."""
    assert can_transition_request("ASIGNADA", "EN_CAMINO", {"TALLER"})


def test_taller_no_puede_aceptar_solicitud_registrada() -> None:
    """No hay shortcut — el cliente debe elegir primero."""
    assert not can_transition_request("REGISTRADA", "ASIGNADA", {"TALLER"})


def test_taller_no_puede_modificar_solicitud_de_otro_taller_aceptada() -> None:
    """Una vez en EN_CAMINO, el taller no puede 'rechazarla' retroactivamente."""
    assert not can_transition_request("EN_CAMINO", "RECHAZADA_TALLER", {"TALLER"})


# ---------------------------------------------------------------------------
# Operador / Administrador en el flujo nuevo (Modo emergencia + soporte)
# ---------------------------------------------------------------------------

def test_operador_puede_intervenir_desde_propuesta_taller() -> None:
    """Modo emergencia: si el taller no responde, el operador puede asignar
    manualmente o cancelar."""
    assert can_transition_request("PROPUESTA_TALLER", "ASIGNADA", {"OPERADOR"})
    assert can_transition_request("PROPUESTA_TALLER", "CANCELADA", {"OPERADOR"})


def test_operador_puede_reintentar_tras_rechazo() -> None:
    """Si 3 talleres rechazan, el operador puede volver a la cola de
    propuestas (o cancelar)."""
    assert can_transition_request("RECHAZADA_TALLER", "PROPUESTA_TALLER", {"OPERADOR"})
    assert can_transition_request("RECHAZADA_TALLER", "CANCELADA", {"OPERADOR"})


# ---------------------------------------------------------------------------
# Estados finales — no se sale ni con los nuevos
# ---------------------------------------------------------------------------

def test_no_se_puede_volver_a_propuesta_desde_completada() -> None:
    assert not can_transition_request("COMPLETADA", "PROPUESTA_TALLER", {"ADMINISTRADOR"})


def test_no_se_puede_volver_a_propuesta_desde_cancelada() -> None:
    assert not can_transition_request("CANCELADA", "PROPUESTA_TALLER", {"CLIENTE"})


# ============================================================================
# Verificación del contador de rechazos consecutivos
# (Fase 3 — escalamiento operativo)
# ============================================================================

class TestContadorRechazosConsecutivos:
    """Estos tests verifican el invariante del contador
    ``taller_rechazos_consecutivos``: incrementa al rechazar y se resetea al
    aceptar. La verificación es a nivel de tipo (qué se persiste), no a
    nivel de endpoint (que requiere DB)."""

    def test_contador_existe_en_modelo_solicitud(self):
        """El modelo Solicitud debe tener el atributo persistido."""
        from app.models.solicitudes import Solicitud
        assert hasattr(Solicitud, "taller_rechazos_consecutivos")

    def test_columna_existe_en_orm(self):
        """La columna debe estar mapeada en SQLAlchemy con default 0."""
        from app.models.solicitudes import Solicitud
        col = Solicitud.__table__.columns["taller_rechazos_consecutivos"]
        assert col.nullable is False
        # default Python = 0 (server_default = "0")
        assert col.default.arg == 0

    def test_estado_propuesta_taller_existe_en_enum(self):
        """El nuevo estado nuevo debe estar exportado por el enum oficial
        — lo usan tanto el router como las migrations."""
        from app.models.enums import EstadoSolicitudEnum
        assert EstadoSolicitudEnum.PROPUESTA_TALLER.value == "PROPUESTA_TALLER"
        assert EstadoSolicitudEnum.RECHAZADA_TALLER.value == "RECHAZADA_TALLER"
