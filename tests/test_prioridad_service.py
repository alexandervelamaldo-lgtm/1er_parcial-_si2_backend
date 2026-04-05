import pytest

from app.models.enums import PrioridadSolicitud
from app.routers.solicitudes import can_transition_request, estimate_eta_minutes
from app.services.multimodal_ai_service import analyze_image_file, transcribe_audio_file
from app.services.payment_service import calculate_payment_breakdown
from app.services.prioridad_service import calcular_prioridad
from app.services.triage_service import analyze_incident


def test_prioridad_critica_en_carretera_de_madrugada() -> None:
    prioridad = calcular_prioridad(
        tipo_incidente="Accidente",
        es_carretera=True,
        condicion_vehiculo="Vehículo inmovilizado",
        nivel_riesgo=5,
    )
    assert prioridad == PrioridadSolicitud.CRITICA


def test_prioridad_media_para_incidente_menor() -> None:
    prioridad = calcular_prioridad(
        tipo_incidente="Llanta ponchada",
        es_carretera=False,
        condicion_vehiculo="Operativo con limitaciones",
        nivel_riesgo=2,
    )
    assert prioridad in {PrioridadSolicitud.BAJA, PrioridadSolicitud.MEDIA}


def test_tecnico_no_puede_saltar_directo_a_completada() -> None:
    assert not can_transition_request("ASIGNADA", "COMPLETADA", {"TECNICO"})


def test_operador_puede_pasar_de_en_camino_a_en_atencion() -> None:
    assert can_transition_request("EN_CAMINO", "EN_ATENCION", {"OPERADOR"})


def test_eta_estimado_tiene_minimo_operativo() -> None:
    assert estimate_eta_minutes(0.5) == 5


def test_triage_baja_confianza_requiere_revision_manual() -> None:
    triage = analyze_incident(
        tipo_incidente="Llanta ponchada",
        descripcion="Auto detenido",
        es_carretera=False,
        condicion_vehiculo="Operativo",
        nivel_riesgo=1,
    )
    assert triage.requires_manual_review


def test_triage_detecta_etiquetas_relevantes() -> None:
    triage = analyze_incident(
        tipo_incidente="Falla mecánica",
        descripcion="El tablero muestra check engine y el motor vibra con humo",
        es_carretera=True,
        condicion_vehiculo="No arranca",
        nivel_riesgo=4,
    )
    assert triage.detected_tags
    assert "motor" in triage.detected_tags
    assert "check_engine" in triage.detected_tags


@pytest.mark.asyncio
async def test_transcripcion_mock_detecta_senales_por_nombre() -> None:
    result = await transcribe_audio_file("audio_bateria_motor.wav", "audio/wav", 1024)
    assert result.provider == "mock"
    assert "bateria" in result.transcript.lower()


@pytest.mark.asyncio
async def test_vision_mock_detecta_llanta_en_contexto() -> None:
    result = await analyze_image_file("foto.png", "image/png", "Vehículo con llanta ponchada en carretera")
    assert "llanta" in result.labels
    assert result.confidence >= 0.7


def test_desglose_de_pago_calcula_comision_plataforma() -> None:
    breakdown = calculate_payment_breakdown(500)
    assert breakdown.commission == 50
    assert breakdown.workshop_amount == 450
