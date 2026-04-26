import pytest

from app.models.enums import PrioridadSolicitud
from app.routers.solicitudes import can_transition_request, estimate_eta_minutes
from app.services.multimodal_ai_service import analyze_image_file, transcribe_audio_file
from app.services.payment_service import calculate_payment_breakdown
from app.services.prioridad_service import calcular_prioridad
from app.services.triage_service import analyze_incident, estimate_repair_cost


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


def test_estimacion_de_costo_incrementa_para_incidente_grave() -> None:
    estimate = estimate_repair_cost(
        tipo_incidente="Accidente",
        descripcion="Choque con humo en carretera y posible daño de motor",
        es_carretera=True,
        condicion_vehiculo="Vehículo inmovilizado",
        nivel_riesgo=5,
        detected_tags=["choque", "motor"],
        clasificacion_confianza=0.84,
        prioridad="CRITICA",
        resumen_ia="Atención prioritaria",
    )
    assert estimate.amount >= 1500
    assert estimate.max_amount > estimate.min_amount
    assert estimate.confidence >= 0.8


def test_estimacion_de_costo_para_bateria_es_moderada() -> None:
    estimate = estimate_repair_cost(
        tipo_incidente="Batería descargada",
        descripcion="El auto no arranca en el estacionamiento",
        es_carretera=False,
        condicion_vehiculo="No arranca",
        nivel_riesgo=2,
        detected_tags=["bateria"],
        clasificacion_confianza=0.7,
        prioridad="MEDIA",
    )
    assert 200 <= estimate.amount <= 700
    assert "Señales consideradas" in estimate.note


def test_estimacion_bolivia_sube_para_vehiculo_antiguo() -> None:
    estimate_old = estimate_repair_cost(
        tipo_incidente="Falla mecánica",
        descripcion="Motor con vibración y humo",
        es_carretera=False,
        condicion_vehiculo="Operativo con limitaciones",
        nivel_riesgo=3,
        prioridad="MEDIA",
        vehiculo_marca="Toyota",
        vehiculo_modelo="Corolla",
        vehiculo_anio=2008,
        region_hint="La Paz",
        detected_tags=["motor"],
    )
    estimate_new = estimate_repair_cost(
        tipo_incidente="Falla mecánica",
        descripcion="Motor con vibración y humo",
        es_carretera=False,
        condicion_vehiculo="Operativo con limitaciones",
        nivel_riesgo=3,
        prioridad="MEDIA",
        vehiculo_marca="Toyota",
        vehiculo_modelo="Corolla",
        vehiculo_anio=2023,
        region_hint="La Paz",
        detected_tags=["motor"],
    )
    assert estimate_old.amount > estimate_new.amount


def test_estimacion_bolivia_refleja_complejidad_por_marca() -> None:
    estimate_economic = estimate_repair_cost(
        tipo_incidente="Accidente",
        descripcion="Colisión frontal leve",
        es_carretera=False,
        condicion_vehiculo="Operativo con limitaciones",
        nivel_riesgo=3,
        prioridad="ALTA",
        vehiculo_marca="Suzuki",
        vehiculo_modelo="Swift",
        vehiculo_anio=2018,
        region_hint="Cochabamba",
        detected_tags=["choque"],
    )
    estimate_premium = estimate_repair_cost(
        tipo_incidente="Accidente",
        descripcion="Colisión frontal leve",
        es_carretera=False,
        condicion_vehiculo="Operativo con limitaciones",
        nivel_riesgo=3,
        prioridad="ALTA",
        vehiculo_marca="BMW",
        vehiculo_modelo="X3",
        vehiculo_anio=2018,
        region_hint="Cochabamba",
        detected_tags=["choque"],
    )
    assert estimate_premium.amount > estimate_economic.amount


def test_estimacion_multimodal_aumenta_con_evidencia_visual_severa() -> None:
    without_visual = estimate_repair_cost(
        tipo_incidente="Accidente",
        descripcion="Choque lateral",
        es_carretera=False,
        condicion_vehiculo="Operativo con limitaciones",
        nivel_riesgo=3,
        prioridad="ALTA",
        vehiculo_marca="Toyota",
        vehiculo_modelo="Corolla",
        vehiculo_anio=2018,
        region_hint="Santa Cruz",
        detected_tags=["choque"],
    )
    with_visual = estimate_repair_cost(
        tipo_incidente="Accidente",
        descripcion="Choque lateral",
        es_carretera=False,
        condicion_vehiculo="Operativo con limitaciones",
        nivel_riesgo=3,
        prioridad="ALTA",
        vehiculo_marca="Toyota",
        vehiculo_modelo="Corolla",
        vehiculo_anio=2018,
        region_hint="Santa Cruz",
        detected_tags=["choque"],
        visual_signals=[
            {
                "labels": ["choque", "motor"],
                "components": ["parachoques", "faro", "radiador"],
                "severity": "SEVERO",
                "visual_factor": 1.28,
                "confidence": 0.83,
            }
        ],
    )
    assert with_visual.amount > without_visual.amount
    assert with_visual.visual_factor >= 1.22
    assert with_visual.visual_confidence >= 0.8


def test_estimacion_multimodal_agrega_multiples_imagenes_por_severidad_maxima() -> None:
    estimate = estimate_repair_cost(
        tipo_incidente="Accidente",
        descripcion="Golpe frontal y lateral",
        es_carretera=True,
        condicion_vehiculo="Vehículo inmovilizado",
        nivel_riesgo=4,
        prioridad="CRITICA",
        vehiculo_marca="BMW",
        vehiculo_modelo="X3",
        vehiculo_anio=2021,
        region_hint="Santa Cruz",
        detected_tags=["choque"],
        visual_signals=[
            {"labels": ["choque"], "components": ["faro"], "severity": "MODERADO", "visual_factor": 1.12, "confidence": 0.71},
            {"labels": ["choque", "motor"], "components": ["radiador", "capo"], "severity": "CRITICO", "visual_factor": 1.4, "confidence": 0.88},
        ],
    )
    assert estimate.visual_factor >= 1.35
    assert "Imágenes analizadas=2" in estimate.note
