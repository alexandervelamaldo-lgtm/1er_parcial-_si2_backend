import csv
import mimetypes
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import desc, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import (
    get_current_cliente_id,
    get_current_taller_id,
    get_current_tecnico_id,
    get_current_user,
    get_role_names,
    require_roles,
)
from app.models.clientes import Cliente
from app.models.disputas import DisputaSolicitud
from app.models.device_tokens import UserDeviceToken
from app.models.evidencias import EvidenciaSolicitud
from app.models.estados_solicitud import EstadoSolicitud
from app.models.historial_eventos import HistorialEvento
from app.models.notificaciones import Notificacion
from app.models.operadores import Operador
from app.models.pagos import PagoSolicitud
from app.models.solicitudes import Solicitud
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.tipos_incidente import TipoIncidente
from app.models.users import User
from app.models.vehiculos import Vehiculo
from app.models.roles import Role
from app.schemas.disputas import DisputaCreate, DisputaResolverRequest, DisputaResponse
from app.schemas.evidencias import EvidenciaResponse
from app.schemas.historial_eventos import HistorialEventoResponse
from app.schemas.pagos import PagoCreate, PagoResponse
from app.schemas.solicitudes import (
    EstadoSolicitudOptionResponse,
    SolicitudAsignar,
    SolicitudCancelarRequest,
    SolicitudCandidatosResponse,
    SolicitudCreate,
    SolicitudDetalleResponse,
    SolicitudEstadoUpdate,
    SolicitudRespuestaClienteRequest,
    SolicitudRevisionManualRequest,
    SolicitudResponderAsignacionRequest,
    SolicitudResponse,
    SolicitudSeguimientoResponse,
    SolicitudTrabajoFinalizadoRequest,
    TecnicoCandidatoResponse,
    TrabajoRealizadoItemResponse,
    TrabajoRealizadoListResponse,
    TrabajoRealizadoResumenResponse,
)
from app.schemas.tipos_incidente import TipoIncidenteResponse
from app.services.invoice_pdf_service import build_invoice_pdf, format_bs
from app.services.payment_service import calculate_payment_breakdown
from app.services.multimodal_ai_service import analyze_image_file, transcribe_audio_file
from app.services.notificacion_service import enviar_notificacion_push
from app.schemas.talleres import TallerResponse
from app.services.prioridad_service import calcular_prioridad
from app.services.triage_service import analyze_incident, estimate_repair_cost
from app.utils.auth import get_subject_from_token
from app.utils.geo import calcular_distancia_km


router = APIRouter(prefix="/solicitudes", tags=["Solicitudes"])

ESTADOS_FINALES = {"COMPLETADA", "CANCELADA"}
ALLOWED_EVIDENCE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",
    "text/plain",
}
EVIDENCE_STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage" / "evidencias"
TRANSICIONES_OPERATIVAS = {
    "REGISTRADA": {"ASIGNADA", "CANCELADA"},
    "ASIGNADA": {"EN_CAMINO", "CANCELADA"},
    "EN_CAMINO": {"EN_ATENCION", "CANCELADA"},
    "EN_ATENCION": {"COMPLETADA", "CANCELADA"},
}


async def _get_estado_por_nombre(db: AsyncSession, nombre: str) -> EstadoSolicitud:
    estado = await db.scalar(select(EstadoSolicitud).where(EstadoSolicitud.nombre == nombre))
    if not estado:
        raise HTTPException(status_code=404, detail=f"Estado {nombre} no encontrado")
    return estado


async def _get_operador_user_ids(db: AsyncSession) -> list[int]:
    result = await db.execute(select(Operador.user_id))
    return list(result.scalars().all())


async def _get_admin_user_ids(db: AsyncSession) -> list[int]:
    result = await db.execute(
        select(User.id).join(User.roles).where(Role.name == "ADMINISTRADOR")
    )
    return list(result.scalars().all())


def estimate_eta_minutes(distance_km: float) -> int:
    return max(5, round((distance_km / 35) * 60))


def can_transition_request(current_state: str, new_state: str, roles: set[str]) -> bool:
    if current_state == new_state:
        return True
    if current_state in ESTADOS_FINALES:
        return False
    allowed = TRANSICIONES_OPERATIVAS.get(current_state, set())
    if roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        return new_state in allowed
    if "TECNICO" in roles:
        if current_state == "ASIGNADA":
            return new_state == "EN_CAMINO"
        if current_state == "EN_CAMINO":
            return new_state == "EN_ATENCION"
    return False


async def _load_request_with_relations(db: AsyncSession, solicitud_id: int) -> Solicitud | None:
    result = await db.execute(
        select(Solicitud)
        .options(
            selectinload(Solicitud.estado),
            selectinload(Solicitud.tipo_incidente),
            selectinload(Solicitud.cliente).selectinload(Cliente.user),
            selectinload(Solicitud.vehiculo),
            selectinload(Solicitud.historial),
            selectinload(Solicitud.tecnico),
            selectinload(Solicitud.taller),
            selectinload(Solicitud.evidencias),
            selectinload(Solicitud.pagos),
            selectinload(Solicitud.disputas),
        )
        .where(Solicitud.id == solicitud_id)
    )
    return result.scalar_one_or_none()


def _serialize_services(services: str) -> list[str]:
    return [item for item in services.split("|") if item]


def _parse_ai_tags(tags: str | None) -> list[str]:
    return [item for item in (tags or "").split("|") if item]


def _merge_ai_tags(existing_tags: str | None, new_tags: list[str]) -> str | None:
    merged = sorted(set(_parse_ai_tags(existing_tags) + [tag for tag in new_tags if tag]))
    return "|".join(merged) if merged else None


def _region_hint_from_request(solicitud: Solicitud) -> str | None:
    candidates = [
        (solicitud.cliente.direccion if solicitud.cliente else None),
        solicitud.descripcion,
    ]
    for raw in candidates:
        if raw and raw.strip():
            return raw.strip()
    return None


def _apply_cost_estimate(solicitud: Solicitud) -> None:
    estimation = estimate_repair_cost(
        tipo_incidente=solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else "Incidente",
        descripcion=solicitud.descripcion,
        es_carretera=solicitud.es_carretera,
        condicion_vehiculo=solicitud.condicion_vehiculo,
        nivel_riesgo=solicitud.nivel_riesgo,
        detected_tags=_parse_ai_tags(solicitud.etiquetas_ia),
        clasificacion_confianza=solicitud.clasificacion_confianza,
        requiere_revision_manual=solicitud.requiere_revision_manual,
        prioridad=solicitud.prioridad.value,
        transcripcion_audio=solicitud.transcripcion_audio,
        resumen_ia=solicitud.resumen_ia,
        vehiculo_marca=solicitud.vehiculo.marca if solicitud.vehiculo else None,
        vehiculo_modelo=solicitud.vehiculo.modelo if solicitud.vehiculo else None,
        vehiculo_anio=solicitud.vehiculo.anio if solicitud.vehiculo else None,
        region_hint=_region_hint_from_request(solicitud),
    )
    solicitud.costo_estimado = estimation.amount
    solicitud.costo_estimado_min = estimation.min_amount
    solicitud.costo_estimado_max = estimation.max_amount
    solicitud.costo_estimacion_confianza = estimation.confidence
    solicitud.costo_estimacion_nota = estimation.note
    solicitud.requiere_revision_manual = solicitud.requiere_revision_manual or estimation.confidence < 0.65


def _resolve_payment_amount(solicitud: Solicitud, requested_amount: float | None) -> float:
    if solicitud.costo_final is not None:
        final_amount = round(solicitud.costo_final, 2)
        if requested_amount is not None and round(requested_amount, 2) != final_amount:
            raise HTTPException(
                status_code=400,
                detail="El monto a pagar debe coincidir con el costo final registrado por el técnico.",
            )
        return final_amount
    if requested_amount is not None:
        return requested_amount
    if solicitud.costo_estimado is not None:
        return solicitud.costo_estimado
    raise HTTPException(
        status_code=400,
        detail="No hay un monto estimado disponible. Indica un monto manual para registrar el pago.",
    )


async def _resolve_user_from_request(request: Request, db: AsyncSession) -> User:
    auth_header = request.headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.lower().startswith("bearer ") else ""
    if not token:
        token = request.query_params.get("access_token", "").strip()
    email = get_subject_from_token(token)
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    result = await db.execute(select(User).options(selectinload(User.roles)).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no autorizado")
    return user


def _build_evidence_api_path(evidence_id: int) -> str:
    return f"/solicitudes/evidencias/{evidence_id}/archivo"


async def _resolve_actor_ids(db: AsyncSession, user: User) -> tuple[int | None, int | None, int | None]:
    roles = get_role_names(user)
    cliente_id: int | None = None
    tecnico_id: int | None = None
    taller_id: int | None = None

    if "CLIENTE" in roles:
        cliente_id = await db.scalar(select(Cliente.id).where(Cliente.user_id == user.id))
    if "TECNICO" in roles:
        tecnico_id = await db.scalar(select(Tecnico.id).where(Tecnico.user_id == user.id))
    if "TALLER" in roles:
        taller_id = await db.scalar(select(Taller.id).where(Taller.user_id == user.id))

    return cliente_id, tecnico_id, taller_id


def _evidence_to_response(evidence: EvidenciaSolicitud) -> EvidenciaResponse:
    base = EvidenciaResponse.model_validate(evidence)
    if evidence.tipo in {"IMAGE", "AUDIO"}:
        return base.model_copy(update={"url": _build_evidence_api_path(evidence.id)})
    return base


def _get_latest_paid_payment(solicitud: Solicitud) -> PagoSolicitud | None:
    paid_payments = [item for item in solicitud.pagos if item.estado == "PAGADO"]
    if not paid_payments:
        return None
    return sorted(paid_payments, key=lambda item: item.fecha_pago or item.fecha_creacion, reverse=True)[0]


def _parse_datetime_query(value: str | None, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    parsed = datetime.fromisoformat(raw) if "T" in raw else datetime.fromisoformat(f"{raw}T00:00:00")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if end_of_day and "T" not in raw:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed


async def _fetch_trabajos_realizados(
    db: AsyncSession,
    desde: str | None,
    hasta: str | None,
    tecnico_id: int | None,
    taller_id: int | None,
) -> TrabajoRealizadoListResponse:
    start = _parse_datetime_query(desde, end_of_day=False)
    end = _parse_datetime_query(hasta, end_of_day=True)

    paid_exists = exists(
        select(PagoSolicitud.id).where(
            PagoSolicitud.solicitud_id == Solicitud.id,
            PagoSolicitud.estado == "PAGADO",
        )
    )
    query = (
        select(Solicitud)
        .options(
            selectinload(Solicitud.tipo_incidente),
            selectinload(Solicitud.estado),
            selectinload(Solicitud.cliente),
            selectinload(Solicitud.taller),
            selectinload(Solicitud.tecnico),
            selectinload(Solicitud.pagos),
        )
        .where(
            Solicitud.trabajo_terminado.is_(True),
            Solicitud.costo_final.is_not(None),
            Solicitud.fecha_cierre.is_not(None),
            paid_exists,
        )
        .order_by(desc(Solicitud.fecha_cierre))
    )
    if tecnico_id is not None:
        query = query.where(Solicitud.tecnico_id == tecnico_id)
    if taller_id is not None:
        query = query.where(Solicitud.taller_id == taller_id)
    if start is not None:
        query = query.where(Solicitud.fecha_cierre >= start)
    if end is not None:
        query = query.where(Solicitud.fecha_cierre <= end)

    result = await db.execute(query)
    solicitudes = list(result.scalars().all())

    items: list[TrabajoRealizadoItemResponse] = []
    total_facturado = 0.0
    total_comision = 0.0
    total_taller = 0.0

    for solicitud in solicitudes:
        pago = _get_latest_paid_payment(solicitud)
        if not pago:
            continue
        item = TrabajoRealizadoItemResponse(
            solicitud_id=solicitud.id,
            fecha_cierre=solicitud.fecha_cierre or datetime.now(timezone.utc),
            cliente=(solicitud.cliente.nombre if solicitud.cliente else "Cliente"),
            taller=(solicitud.taller.nombre if solicitud.taller else "Sin taller"),
            tecnico=(solicitud.tecnico.nombre if solicitud.tecnico else "Sin tecnico"),
            tipo_incidente=(solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else "Incidente"),
            costo_estimado=solicitud.costo_estimado,
            costo_final=round(float(solicitud.costo_final or 0), 2),
            monto_total=round(float(pago.monto_total or 0), 2),
            monto_comision=round(float(pago.monto_comision or 0), 2),
            monto_taller=round(float(pago.monto_taller or 0), 2),
            metodo_pago=pago.metodo_pago,
            estado_pago=pago.estado,
        )
        items.append(item)
        total_facturado += item.monto_total
        total_comision += item.monto_comision
        total_taller += item.monto_taller

    cantidad = len(items)
    promedio = round(total_facturado / cantidad, 2) if cantidad else 0.0
    resumen = TrabajoRealizadoResumenResponse(
        cantidad_trabajos=cantidad,
        total_facturado=round(total_facturado, 2),
        total_comision=round(total_comision, 2),
        total_taller=round(total_taller, 2),
        promedio_por_trabajo=promedio,
    )
    return TrabajoRealizadoListResponse(items=items, resumen=resumen)


def _is_client_approval_expired(solicitud: Solicitud) -> bool:
    if not solicitud.propuesta_expira_en or solicitud.cliente_aprobada is not False:
        return False
    reference_time = (
        solicitud.propuesta_expira_en
        if solicitud.propuesta_expira_en.tzinfo
        else solicitud.propuesta_expira_en.replace(tzinfo=timezone.utc)
    )
    return datetime.now(timezone.utc) > reference_time


def _keyword_matches_for_workshop(solicitud: Solicitud, services: list[str]) -> tuple[bool, list[str]]:
    normalized_services = " ".join(services).lower()
    source_text = " ".join(
        [
            solicitud.descripcion or "",
            solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else "",
            solicitud.etiquetas_ia or "",
        ]
    ).lower()
    keywords = {
        "electrico": ["bateria", "alternador", "corriente", "check_engine"],
        "llantas": ["llanta", "neumatico", "ponchada", "pinchada"],
        "mecanica": ["motor", "aceite", "falla mecanica", "check_engine"],
        "grua": ["choque", "accidente", "remolque"],
        "combustible": ["combustible", "gasolina", "diesel"],
    }
    matched = [service for service, aliases in keywords.items() if service in normalized_services and any(alias in source_text for alias in aliases)]
    return bool(matched), matched


async def _dispatch_push_notifications(db: AsyncSession, user_ids: list[int], titulo: str, mensaje: str, tipo: str) -> None:
    if not user_ids:
        return
    result = await db.execute(select(UserDeviceToken).where(UserDeviceToken.user_id.in_(list(set(user_ids)))))
    for device_token in result.scalars().all():
        enviar_notificacion_push(
            device_token.token,
            titulo,
            mensaje,
            {"type": tipo, "user_id": str(device_token.user_id)},
        )


async def _notify_users(
    db: AsyncSession,
    user_ids: list[int],
    titulo: str,
    mensaje: str,
    tipo: str,
) -> None:
    for user_id in set(user_ids):
        db.add(
            Notificacion(
                usuario_id=user_id,
                titulo=titulo,
                mensaje=mensaje,
                tipo=tipo,
            )
        )
    await _dispatch_push_notifications(db, user_ids, titulo, mensaje, tipo)


async def _get_candidate_workshops(
    db: AsyncSession,
    solicitud: Solicitud,
    radio_km: float,
) -> list[TallerResponse]:
    result = await db.execute(select(Taller))
    talleres = result.scalars().all()
    encontrados: list[TallerResponse] = []
    for taller in talleres:
        if not taller.disponible:
            continue
        services = _serialize_services(taller.servicios)
        match_especializacion, matched_services = _keyword_matches_for_workshop(solicitud, services)
        distancia = calcular_distancia_km(
            solicitud.latitud_incidente,
            solicitud.longitud_incidente,
            taller.latitud,
            taller.longitud,
        )
        if distancia <= radio_km:
            prioridad_bonus = {
                "CRITICA": 15,
                "ALTA": 10,
                "MEDIA": 5,
                "BAJA": 0,
            }.get(solicitud.prioridad.value, 0)
            score = round((40 if match_especializacion else 0) + max(0, 35 - distancia) + min(taller.capacidad, 10) + prioridad_bonus, 2)
            encontrados.append(
                TallerResponse(
                    id=taller.id,
                    nombre=taller.nombre,
                    direccion=taller.direccion,
                    latitud=taller.latitud,
                    longitud=taller.longitud,
                    telefono=taller.telefono,
                    capacidad=taller.capacidad,
                    servicios=services,
                    disponible=taller.disponible,
                    acepta_automaticamente=taller.acepta_automaticamente,
                    user_id=taller.user_id,
                    distancia_km=round(distancia, 2),
                    score=score,
                    match_especializacion=match_especializacion,
                    motivo_sugerencia=(
                        f"Especialización compatible: {', '.join(matched_services)}"
                        if matched_services
                        else "Se prioriza cercanía y disponibilidad operativa"
                    ),
                )
            )
    return sorted(encontrados, key=lambda item: ((item.score or 0) * -1, item.distancia_km or 0))


async def _get_candidate_technicians(
    db: AsyncSession,
    solicitud: Solicitud,
    radio_km: float,
) -> list[TecnicoCandidatoResponse]:
    result = await db.execute(select(Tecnico).where(Tecnico.disponibilidad.is_(True)))
    tecnicos = result.scalars().all()
    encontrados: list[TecnicoCandidatoResponse] = []
    for tecnico in tecnicos:
        if tecnico.latitud_actual is None or tecnico.longitud_actual is None:
            continue
        distancia = calcular_distancia_km(
            solicitud.latitud_incidente,
            solicitud.longitud_incidente,
            tecnico.latitud_actual,
            tecnico.longitud_actual,
        )
        if distancia <= radio_km:
            encontrados.append(
                TecnicoCandidatoResponse(
                    id=tecnico.id,
                    nombre=tecnico.nombre,
                    telefono=tecnico.telefono,
                    especialidad=tecnico.especialidad,
                    disponibilidad=tecnico.disponibilidad,
                    distancia_km=round(distancia, 2),
                    eta_min=estimate_eta_minutes(distancia),
                )
            )
    return sorted(encontrados, key=lambda item: item.distancia_km or 0)


def _build_tracking_response(solicitud: Solicitud) -> SolicitudSeguimientoResponse:
    estado = solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO"
    tecnico = solicitud.tecnico
    propuesta_expirada = _is_client_approval_expired(solicitud)
    if solicitud.taller_id and solicitud.cliente_aprobada is False and not tecnico:
        return SolicitudSeguimientoResponse(
            solicitud_id=solicitud.id,
            estado=estado,
            taller_nombre=solicitud.taller.nombre if solicitud.taller else None,
            cliente_aprobada=solicitud.cliente_aprobada,
            propuesta_expira_en=solicitud.propuesta_expira_en,
            propuesta_expirada=propuesta_expirada,
            tracking_activo=False,
            mensaje=(
                "La propuesta está pendiente de aprobación del cliente."
                if not propuesta_expirada
                else "La propuesta expiró y requiere una nueva asignación."
            ),
        )
    if not tecnico:
        return SolicitudSeguimientoResponse(
            solicitud_id=solicitud.id,
            estado=estado,
            taller_nombre=solicitud.taller.nombre if solicitud.taller else None,
            cliente_aprobada=solicitud.cliente_aprobada,
            propuesta_expira_en=solicitud.propuesta_expira_en,
            propuesta_expirada=propuesta_expirada,
            tracking_activo=False,
            mensaje="La solicitud aún no tiene un técnico confirmado.",
        )
    if tecnico.latitud_actual is None or tecnico.longitud_actual is None:
        return SolicitudSeguimientoResponse(
            solicitud_id=solicitud.id,
            estado=estado,
            taller_nombre=solicitud.taller.nombre if solicitud.taller else None,
            tecnico_id=tecnico.id,
            tecnico_nombre=tecnico.nombre,
            cliente_aprobada=solicitud.cliente_aprobada,
            propuesta_expira_en=solicitud.propuesta_expira_en,
            propuesta_expirada=propuesta_expirada,
            tracking_activo=False,
            requiere_compartir_ubicacion=True,
            mensaje="El técnico todavía no comparte su ubicación actual.",
        )
    distancia = calcular_distancia_km(
        solicitud.latitud_incidente,
        solicitud.longitud_incidente,
        tecnico.latitud_actual,
        tecnico.longitud_actual,
    )
    location_updated_at = tecnico.ubicacion_actualizada_en
    is_stale = False
    if location_updated_at is not None:
        reference_time = location_updated_at if location_updated_at.tzinfo else location_updated_at.replace(tzinfo=timezone.utc)
        is_stale = datetime.now(timezone.utc) - reference_time > timedelta(minutes=15)
    return SolicitudSeguimientoResponse(
        solicitud_id=solicitud.id,
        estado=estado,
        taller_nombre=solicitud.taller.nombre if solicitud.taller else None,
        tecnico_id=tecnico.id,
        tecnico_nombre=tecnico.nombre,
        latitud_actual=tecnico.latitud_actual,
        longitud_actual=tecnico.longitud_actual,
        distancia_km=round(distancia, 2),
        eta_min=estimate_eta_minutes(distancia),
        ubicacion_actualizada_en=tecnico.ubicacion_actualizada_en,
        ubicacion_desactualizada=is_stale,
        tracking_activo=not is_stale,
        sin_senal=is_stale,
        cliente_aprobada=solicitud.cliente_aprobada,
        propuesta_expira_en=solicitud.propuesta_expira_en,
        propuesta_expirada=propuesta_expirada,
        mensaje=(
            "La ubicación del técnico puede estar desactualizada o sin señal reciente."
            if is_stale
            else "Seguimiento en tiempo real estimado según la última ubicación reportada."
        ),
    )


def validate_request_access(
    current_user: User,
    current_cliente_id: int | None,
    current_tecnico_id: int | None,
    current_taller_id: int | None,
    solicitud: Solicitud,
) -> None:
    roles = get_role_names(current_user)
    if roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        return
    if "CLIENTE" in roles and current_cliente_id == solicitud.cliente_id:
        return
    if "TECNICO" in roles and current_tecnico_id == solicitud.tecnico_id:
        return
    if "TALLER" in roles and current_taller_id == solicitud.taller_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes acceder a esta solicitud")


@router.post("", response_model=SolicitudResponse, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: SolicitudCreate,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    if "CLIENTE" in get_role_names(current_user) and payload.cliente_id != current_cliente_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes crear solicitudes para otro cliente")
    cliente = await db.get(Cliente, payload.cliente_id)
    vehiculo = await db.get(Vehiculo, payload.vehiculo_id)
    tipo_incidente = await db.get(TipoIncidente, payload.tipo_incidente_id)
    if not cliente or not vehiculo or not tipo_incidente:
        raise HTTPException(status_code=400, detail="Cliente, vehículo o tipo de incidente inválido")
    if vehiculo.cliente_id != payload.cliente_id:
        raise HTTPException(status_code=400, detail="El vehículo no pertenece al cliente indicado")

    estado_registrada = await _get_estado_por_nombre(db, "REGISTRADA")
    prioridad = calcular_prioridad(
        tipo_incidente=tipo_incidente.nombre,
        es_carretera=payload.es_carretera,
        condicion_vehiculo=payload.condicion_vehiculo,
        nivel_riesgo=payload.nivel_riesgo,
    )
    triage = analyze_incident(
        tipo_incidente=tipo_incidente.nombre,
        descripcion=payload.descripcion,
        es_carretera=payload.es_carretera,
        condicion_vehiculo=payload.condicion_vehiculo,
        nivel_riesgo=payload.nivel_riesgo,
    )

    solicitud = Solicitud(
        cliente_id=payload.cliente_id,
        vehiculo_id=payload.vehiculo_id,
        taller_id=payload.taller_id,
        tipo_incidente_id=payload.tipo_incidente_id,
        estado_id=estado_registrada.id,
        latitud_incidente=payload.latitud_incidente,
        longitud_incidente=payload.longitud_incidente,
        descripcion=payload.descripcion,
        foto_url=payload.foto_url,
        es_carretera=payload.es_carretera,
        condicion_vehiculo=payload.condicion_vehiculo,
        nivel_riesgo=payload.nivel_riesgo,
        clasificacion_confianza=triage.confidence,
        requiere_revision_manual=triage.requires_manual_review,
        motivo_prioridad=triage.reason,
        resumen_ia=triage.summary,
        etiquetas_ia="|".join(triage.detected_tags),
        proveedor_ia=triage.provider,
        prioridad=prioridad,
    )
    solicitud.tipo_incidente = tipo_incidente
    _apply_cost_estimate(solicitud)
    db.add(solicitud)
    await db.flush()

    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior="NUEVA",
            estado_nuevo=estado_registrada.nombre,
            observacion="Solicitud creada por el cliente",
            usuario_id=cliente.user_id,
        )
    )
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_registrada.nombre,
            estado_nuevo=estado_registrada.nombre,
            observacion=(
                f"Clasificación IA: {triage.summary}. Confianza {triage.confidence:.2f}. "
                f"Etiquetas: {', '.join(triage.detected_tags) or 'sin etiquetas concluyentes'}. "
                f"Costo estimado aproximado: {format_bs(solicitud.costo_estimado)}"
            ),
            usuario_id=cliente.user_id,
        )
    )
    db.add(
        Notificacion(
            usuario_id=cliente.user_id,
            titulo="Solicitud registrada",
            mensaje=f"Tu solicitud #{solicitud.id} fue registrada con prioridad {prioridad.value}.",
            tipo="SOLICITUD_REGISTRADA",
        )
    )
    if triage.requires_manual_review:
        operador_ids = await _get_operador_user_ids(db)
        await _notify_users(
            db,
            operador_ids,
            "Revisión manual requerida",
            f"La solicitud #{solicitud.id} necesita validación operativa por confianza {triage.confidence:.2f}.",
            "REVISION_MANUAL",
        )
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_registrada.nombre,
                estado_nuevo=estado_registrada.nombre,
                observacion="Solicitud derivada a revisión manual por baja confianza",
                usuario_id=cliente.user_id,
            )
        )
    if prioridad.value == "CRITICA":
        operador_ids = await _get_operador_user_ids(db)
        await _notify_users(
            db,
            operador_ids,
            "Incidente crítico escalado",
            f"La solicitud #{solicitud.id} requiere atención inmediata.",
            "ESCALAMIENTO_CRITICO",
        )
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_registrada.nombre,
                estado_nuevo=estado_registrada.nombre,
                observacion="Escalada automática por prioridad crítica",
                usuario_id=cliente.user_id,
            )
        )
    await db.commit()

    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.get("/tipos-incidente", response_model=list[TipoIncidenteResponse])
async def list_incident_types(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TipoIncidente]:
    result = await db.execute(select(TipoIncidente).order_by(TipoIncidente.id))
    return list(result.scalars().all())


@router.get("/estados", response_model=list[EstadoSolicitudOptionResponse])
async def list_request_states(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EstadoSolicitud]:
    result = await db.execute(select(EstadoSolicitud).order_by(EstadoSolicitud.id))
    return list(result.scalars().all())


@router.get("", response_model=list[SolicitudResponse])
async def list_requests(
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> list[Solicitud]:
    query = (
        select(Solicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .order_by(desc(Solicitud.fecha_solicitud))
    )
    roles = get_role_names(current_user)
    if "CLIENTE" in roles and current_cliente_id is not None:
        query = query.where(Solicitud.cliente_id == current_cliente_id)
    elif "TECNICO" in roles and current_tecnico_id is not None:
        query = query.where(Solicitud.tecnico_id == current_tecnico_id)
    elif "TALLER" in roles and current_taller_id is not None:
        query = query.where(Solicitud.taller_id == current_taller_id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/activas", response_model=list[SolicitudResponse])
async def list_active_requests(
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> list[Solicitud]:
    query = (
        select(Solicitud)
        .join(EstadoSolicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .where(EstadoSolicitud.nombre.not_in(["COMPLETADA", "CANCELADA"]))
        .order_by(desc(Solicitud.fecha_solicitud))
    )
    roles = get_role_names(current_user)
    if "CLIENTE" in roles and current_cliente_id is not None:
        query = query.where(Solicitud.cliente_id == current_cliente_id)
    elif "TECNICO" in roles and current_tecnico_id is not None:
        query = query.where(Solicitud.tecnico_id == current_tecnico_id)
    elif "TALLER" in roles and current_taller_id is not None:
        query = query.where(Solicitud.taller_id == current_taller_id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/historial/{cliente_id}", response_model=list[SolicitudResponse])
async def request_history(
    cliente_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> list[Solicitud]:
    if "CLIENTE" in get_role_names(current_user) and cliente_id != current_cliente_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes ver historial de otro cliente")
    result = await db.execute(
        select(Solicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .where(Solicitud.cliente_id == cliente_id)
        .order_by(desc(Solicitud.fecha_solicitud))
    )
    return list(result.scalars().all())


@router.get("/{solicitud_id:int}", response_model=SolicitudResponse)
async def get_request(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    return solicitud


@router.get("/{solicitud_id:int}/detalle", response_model=SolicitudDetalleResponse)
async def get_request_detail(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> SolicitudDetalleResponse:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    historial = [
        HistorialEventoResponse.model_validate(evento)
        for evento in sorted(solicitud.historial, key=lambda item: item.fecha_evento, reverse=True)
    ]
    detalle = SolicitudResponse.model_validate(solicitud).model_dump()
    evidencias = [_evidence_to_response(item) for item in sorted(solicitud.evidencias, key=lambda item: item.fecha_creacion, reverse=True)]
    pagos = [PagoResponse.model_validate(item) for item in sorted(solicitud.pagos, key=lambda item: item.fecha_creacion, reverse=True)]
    disputas = [DisputaResponse.model_validate(item) for item in sorted(solicitud.disputas, key=lambda item: item.fecha_creacion, reverse=True)]
    return SolicitudDetalleResponse(**detalle, historial=historial, evidencias=evidencias, pagos=pagos, disputas=disputas)


@router.get("/{solicitud_id:int}/historial", response_model=list[HistorialEventoResponse])
async def get_request_timeline(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> list[HistorialEvento]:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    return sorted(solicitud.historial, key=lambda item: item.fecha_evento, reverse=True)


@router.get("/{solicitud_id:int}/candidatos", response_model=SolicitudCandidatosResponse)
async def get_request_candidates(
    solicitud_id: int,
    radio_km: float = Query(default=25.0, gt=0, le=200),
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> SolicitudCandidatosResponse:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    talleres = await _get_candidate_workshops(db, solicitud, radio_km)
    tecnicos = await _get_candidate_technicians(db, solicitud, radio_km)
    hay_cobertura = bool(talleres and tecnicos)
    mensaje = None
    if not talleres and not tecnicos:
        mensaje = "No hay talleres ni técnicos disponibles dentro del radio indicado."
    elif not talleres:
        mensaje = "Hay técnicos disponibles, pero no se encontró un taller dentro del radio indicado."
    elif not tecnicos:
        mensaje = "Hay talleres cercanos, pero no hay técnicos disponibles dentro del radio indicado."
    return SolicitudCandidatosResponse(
        solicitud_id=solicitud.id,
        hay_cobertura=hay_cobertura,
        mensaje=mensaje,
        talleres=talleres[:5],
        tecnicos=tecnicos[:5],
    )


@router.get("/{solicitud_id:int}/seguimiento", response_model=SolicitudSeguimientoResponse)
async def get_request_tracking(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> SolicitudSeguimientoResponse:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    return _build_tracking_response(solicitud)


@router.put("/{solicitud_id:int}/asignar", response_model=SolicitudResponse)
async def assign_request(
    solicitud_id: int,
    payload: SolicitudAsignar,
    current_user: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await db.get(Solicitud, solicitud_id)
    tecnico = await db.get(Tecnico, payload.tecnico_id) if payload.tecnico_id is not None else None
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if payload.tecnico_id is not None and not tecnico:
        raise HTTPException(status_code=404, detail="Técnico no encontrado")
    if tecnico and not tecnico.disponibilidad:
        raise HTTPException(status_code=400, detail="El técnico seleccionado no está disponible")
    estado_anterior = await db.get(EstadoSolicitud, solicitud.estado_id)
    if estado_anterior and estado_anterior.nombre in ESTADOS_FINALES:
        raise HTTPException(status_code=400, detail="No puedes reasignar una solicitud cerrada")

    estado_asignada = await _get_estado_por_nombre(db, "ASIGNADA")
    cliente = await db.get(Cliente, solicitud.cliente_id)
    taller_id = payload.taller_id
    if taller_id is None:
        talleres = await _get_candidate_workshops(db, solicitud, radio_km=25)
        taller_id = talleres[0].id if talleres else solicitud.taller_id
    if taller_id is None and tecnico and tecnico.taller_id:
        taller_id = tecnico.taller_id
    taller = await db.get(Taller, taller_id) if taller_id is not None else None
    if tecnico is None and taller is not None:
        tecnico = await db.scalar(
            select(Tecnico)
            .where(Tecnico.taller_id == taller.id, Tecnico.disponibilidad.is_(True))
            .order_by(Tecnico.id)
        )
    if tecnico is None and taller is None:
        tecnico = await db.scalar(select(Tecnico).where(Tecnico.disponibilidad.is_(True)).order_by(Tecnico.id))
        if tecnico and tecnico.taller_id:
            taller = await db.get(Taller, tecnico.taller_id)
            taller_id = tecnico.taller_id
    if taller is None:
        raise HTTPException(
            status_code=400,
            detail="No hay talleres disponibles para esta solicitud. Debe quedar en cola operativa para reintento.",
        )
    if taller and not taller.disponible:
        raise HTTPException(status_code=400, detail="El taller seleccionado no está disponible")
    if solicitud.tecnico_id and (tecnico is None or solicitud.tecnico_id != tecnico.id):
        tecnico_anterior = await db.get(Tecnico, solicitud.tecnico_id)
        if tecnico_anterior:
            tecnico_anterior.disponibilidad = True
    if tecnico and taller and tecnico.taller_id and tecnico.taller_id != taller.id:
        raise HTTPException(status_code=400, detail="El técnico no pertenece al taller seleccionado")
    solicitud.tecnico_id = tecnico.id if tecnico else None
    solicitud.taller_id = taller_id
    solicitud.estado_id = estado_asignada.id
    solicitud.fecha_asignacion = datetime.now(timezone.utc)
    solicitud.cliente_aprobada = False
    solicitud.cliente_aprobacion_observacion = None
    solicitud.cliente_aprobacion_fecha = None
    solicitud.propuesta_expira_en = datetime.now(timezone.utc) + timedelta(minutes=15)
    if tecnico:
        tecnico.disponibilidad = False

    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_anterior.nombre if estado_anterior else "SIN_ESTADO",
            estado_nuevo=estado_asignada.nombre,
            observacion=(
                f"Solicitud propuesta al taller {taller.nombre} y técnico {tecnico.nombre}. Pendiente de aprobación del cliente"
                if tecnico and taller
                else (
                    f"Solicitud propuesta al taller {taller.nombre}. No hay técnico disponible todavía; se reintentará automáticamente"
                    if taller
                    else "Solicitud enviada a proceso de asignación"
                )
            ),
            usuario_id=current_user.id,
        )
    )
    notify_ids: list[int] = []
    if cliente:
        notify_ids.append(cliente.user_id)
    if taller and taller.user_id:
        notify_ids.append(taller.user_id)
    if tecnico:
        notify_ids.append(tecnico.user_id)
    await _notify_users(
        db,
        notify_ids,
        "Asignación generada",
        (
            (
                f"La solicitud #{solicitud.id} fue propuesta al taller {taller.nombre} con el técnico {tecnico.nombre}. Debe ser aprobada por el cliente."
                if tecnico and taller
                else f"La solicitud #{solicitud.id} fue propuesta al taller {taller.nombre}. No hay técnico disponible todavía y se notificará cuando haya cobertura."
            )
            if taller
            else f"La solicitud #{solicitud.id} fue enviada a asignación operativa."
        ),
        "ASIGNACION_TALLER",
    )
    if tecnico is None and taller is not None:
        await _notify_users(
            db,
            await _get_operador_user_ids(db),
            "Sin técnicos disponibles",
            f"La solicitud #{solicitud.id} tiene taller propuesto pero aún no cuenta con técnico disponible.",
            "SIN_TECNICO_DISPONIBLE",
        )
    await db.commit()

    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.put("/{solicitud_id:int}/respuesta-cliente", response_model=SolicitudResponse)
async def respond_client_assignment(
    solicitud_id: int,
    payload: SolicitudRespuestaClienteRequest,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if "CLIENTE" not in get_role_names(current_user) or solicitud.cliente_id != current_cliente_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el cliente propietario puede responder la propuesta")
    estado_actual = await db.get(EstadoSolicitud, solicitud.estado_id)
    if not estado_actual:
        raise HTTPException(status_code=404, detail="Estado actual no encontrado")
    if solicitud.taller_id is None:
        raise HTTPException(status_code=400, detail="La solicitud todavía no tiene una propuesta de taller")
    if _is_client_approval_expired(solicitud):
        estado_registrada = await _get_estado_por_nombre(db, "REGISTRADA")
        solicitud.estado_id = estado_registrada.id
        solicitud.taller_id = None
        solicitud.tecnico_id = None
        solicitud.cliente_aprobada = False
        solicitud.cliente_aprobacion_observacion = "La propuesta expiró antes de ser aprobada"
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre,
                estado_nuevo=estado_registrada.nombre,
                observacion="La propuesta expiró y volvió a cola operativa",
                usuario_id=current_user.id,
            )
        )
        await db.commit()
        result = await _load_request_with_relations(db, solicitud.id)
        if not result:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        return result
    solicitud.cliente_aprobada = payload.aprobada
    solicitud.cliente_aprobacion_observacion = payload.observacion
    solicitud.cliente_aprobacion_fecha = datetime.now(timezone.utc)
    cliente = await db.get(Cliente, solicitud.cliente_id)
    operador_ids = await _get_operador_user_ids(db)
    if payload.aprobada:
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre,
                estado_nuevo=estado_actual.nombre,
                observacion=f"Cliente aprobó la propuesta: {payload.observacion}",
                usuario_id=current_user.id,
            )
        )
        notify_ids = operador_ids
        if solicitud.taller and solicitud.taller.user_id:
            notify_ids.append(solicitud.taller.user_id)
        if solicitud.tecnico:
            notify_ids.append(solicitud.tecnico.user_id)
        await _notify_users(
            db,
            notify_ids,
            "Cliente aprobó la propuesta",
            f"La solicitud #{solicitud.id} fue aprobada por el cliente y puede continuar.",
            "ASIGNACION_APROBADA_CLIENTE",
        )
    else:
        estado_registrada = await _get_estado_por_nombre(db, "REGISTRADA")
        solicitud.estado_id = estado_registrada.id
        if solicitud.tecnico_id and solicitud.tecnico:
            solicitud.tecnico.disponibilidad = True
        solicitud.tecnico_id = None
        previous_taller_name = solicitud.taller.nombre if solicitud.taller else "sin taller"
        solicitud.taller_id = None
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre,
                estado_nuevo=estado_registrada.nombre,
                observacion=f"Cliente rechazó la propuesta de {previous_taller_name}: {payload.observacion}",
                usuario_id=current_user.id,
            )
        )
        await _notify_users(
            db,
            operador_ids,
            "Cliente rechazó la propuesta",
            f"La solicitud #{solicitud.id} requiere una nueva asignación operativa.",
            "ASIGNACION_RECHAZADA_CLIENTE",
        )
    if cliente and cliente.user_id:
        await _notify_users(
            db,
            [cliente.user_id],
            "Respuesta registrada",
            f"Tu respuesta sobre la propuesta de la solicitud #{solicitud.id} fue guardada.",
            "RESPUESTA_PROPUESTA_CLIENTE",
        )
    await db.commit()
    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.put("/{solicitud_id:int}/respuesta-taller", response_model=SolicitudResponse)
async def respond_workshop_assignment(
    solicitud_id: int,
    payload: SolicitudResponderAsignacionRequest,
    current_user: User = Depends(get_current_user),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if "TALLER" not in get_role_names(current_user) or solicitud.taller_id != current_taller_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el taller asignado puede responder")
    if solicitud.cliente_aprobada is False:
        raise HTTPException(status_code=400, detail="La propuesta aún debe ser aprobada por el cliente")
    if _is_client_approval_expired(solicitud):
        raise HTTPException(status_code=400, detail="La propuesta expiró y requiere una nueva asignación")
    estado_actual = await db.get(EstadoSolicitud, solicitud.estado_id)
    if not estado_actual:
        raise HTTPException(status_code=404, detail="Estado actual no encontrado")
    cliente = await db.get(Cliente, solicitud.cliente_id)
    if payload.aceptada:
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre,
                estado_nuevo=estado_actual.nombre,
                observacion=f"Taller confirmó asignación: {payload.observacion}",
                usuario_id=current_user.id,
            )
        )
        if solicitud.tecnico_id is None:
            tecnico = await db.scalar(
                select(Tecnico)
                .where(Tecnico.taller_id == current_taller_id, Tecnico.disponibilidad.is_(True))
                .order_by(Tecnico.id)
            )
            if tecnico:
                solicitud.tecnico_id = tecnico.id
                tecnico.disponibilidad = False
                db.add(
                    HistorialEvento(
                        solicitud_id=solicitud.id,
                        estado_anterior=estado_actual.nombre,
                        estado_nuevo=estado_actual.nombre,
                        observacion=f"Técnico {tecnico.nombre} preasignado por el taller",
                        usuario_id=current_user.id,
                    )
                )
                await _notify_users(
                    db,
                    [tecnico.user_id],
                    "Nueva solicitud del taller",
                    f"Se te preasignó la solicitud #{solicitud.id}.",
                    "ASIGNACION_TECNICO",
                )
    else:
        estado_registrada = await _get_estado_por_nombre(db, "REGISTRADA")
        solicitud.estado_id = estado_registrada.id
        solicitud.cliente_aprobada = None
        solicitud.taller_id = None
        if solicitud.tecnico_id:
            tecnico = await db.get(Tecnico, solicitud.tecnico_id)
            if tecnico:
                tecnico.disponibilidad = True
        solicitud.tecnico_id = None
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre,
                estado_nuevo=estado_registrada.nombre,
                observacion=f"Taller rechazó la asignación: {payload.observacion}",
                usuario_id=current_user.id,
            )
        )
        operator_ids = await _get_operador_user_ids(db)
        notify_ids = operator_ids + ([cliente.user_id] if cliente else [])
        await _notify_users(
            db,
            notify_ids,
            "Taller rechazó la solicitud",
            f"La solicitud #{solicitud.id} regresó a cola por rechazo del taller.",
            "RECHAZO_TALLER",
        )
    await db.commit()
    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.put("/{solicitud_id:int}/revision-manual", response_model=SolicitudResponse)
async def review_request_manually(
    solicitud_id: int,
    payload: SolicitudRevisionManualRequest,
    current_user: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await db.get(Solicitud, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    estado_actual = await db.get(EstadoSolicitud, solicitud.estado_id)
    solicitud.clasificacion_confianza = payload.confianza
    solicitud.prioridad = payload.prioridad
    solicitud.resumen_ia = payload.resumen_ia
    solicitud.motivo_prioridad = payload.motivo_prioridad
    solicitud.requiere_revision_manual = False
    _apply_cost_estimate(solicitud)
    cliente = await db.get(Cliente, solicitud.cliente_id)
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_actual.nombre if estado_actual else "SIN_ESTADO",
            estado_nuevo=estado_actual.nombre if estado_actual else "SIN_ESTADO",
            observacion=(
                f"Revisión manual completada. Prioridad final {payload.prioridad.value}. "
                f"Costo estimado actualizado a {format_bs(solicitud.costo_estimado)}"
            ),
            usuario_id=current_user.id,
        )
    )
    if cliente:
        await _notify_users(
            db,
            [cliente.user_id],
            "Clasificación actualizada",
            f"La solicitud #{solicitud.id} fue validada manualmente por operación.",
            "REVISION_MANUAL_COMPLETADA",
        )
    await db.commit()
    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.put("/{solicitud_id:int}/responder-asignacion", response_model=SolicitudResponse)
async def respond_assignment(
    solicitud_id: int,
    payload: SolicitudResponderAsignacionRequest,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    roles = get_role_names(current_user)
    if "TECNICO" not in roles or solicitud.tecnico_id != current_tecnico_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el técnico asignado puede responder")
    if solicitud.cliente_aprobada is False:
        raise HTTPException(status_code=400, detail="La propuesta aún debe ser aprobada por el cliente")
    if _is_client_approval_expired(solicitud):
        raise HTTPException(status_code=400, detail="La propuesta expiró y requiere una nueva asignación")
    tecnico = solicitud.tecnico
    if not tecnico:
        raise HTTPException(status_code=400, detail="La solicitud no tiene técnico asignado")
    cliente = await db.get(Cliente, solicitud.cliente_id)
    estado_actual = await db.get(EstadoSolicitud, solicitud.estado_id)
    if not estado_actual:
        raise HTTPException(status_code=404, detail="Estado actual no encontrado")

    if payload.aceptada:
        estado_en_camino = await _get_estado_por_nombre(db, "EN_CAMINO")
        solicitud.estado_id = estado_en_camino.id
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre,
                estado_nuevo=estado_en_camino.nombre,
                observacion=payload.observacion,
                usuario_id=current_user.id,
            )
        )
        if cliente:
            await _notify_users(
                db,
                [cliente.user_id],
                "Técnico en camino",
                f"El técnico {tecnico.nombre} confirmó la atención de tu solicitud #{solicitud.id}.",
                "TECNICO_EN_CAMINO",
            )
    else:
        estado_registrada = await _get_estado_por_nombre(db, "REGISTRADA")
        solicitud.estado_id = estado_registrada.id
        solicitud.tecnico_id = None
        solicitud.cliente_aprobada = None
        solicitud.taller_id = None
        tecnico.disponibilidad = True
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre,
                estado_nuevo=estado_registrada.nombre,
                observacion=payload.observacion,
                usuario_id=current_user.id,
            )
        )
        operador_ids = await _get_operador_user_ids(db)
        notify_ids = operador_ids + ([cliente.user_id] if cliente else [])
        await _notify_users(
            db,
            notify_ids,
            "Asignación rechazada",
            f"La solicitud #{solicitud.id} volvió a cola de atención para una nueva propuesta.",
            "ASIGNACION_RECHAZADA",
        )
    await db.commit()

    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.put("/{solicitud_id:int}/cancelar", response_model=SolicitudResponse)
async def cancel_request(
    solicitud_id: int,
    payload: SolicitudCancelarRequest,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    roles = get_role_names(current_user)
    if "CLIENTE" in roles and solicitud.cliente_id != current_cliente_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes cancelar esta solicitud")
    if not roles.intersection({"ADMINISTRADOR", "OPERADOR", "CLIENTE"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes cancelar esta solicitud")
    estado_actual = await db.get(EstadoSolicitud, solicitud.estado_id)
    if not estado_actual:
        raise HTTPException(status_code=404, detail="Estado actual no encontrado")
    if estado_actual.nombre in ESTADOS_FINALES:
        raise HTTPException(status_code=400, detail="La solicitud ya está cerrada")
    if estado_actual.nombre == "EN_ATENCION" and "CLIENTE" in roles and not roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        raise HTTPException(status_code=400, detail="No puedes cancelar una solicitud en atención")
    estado_cancelada = await _get_estado_por_nombre(db, "CANCELADA")
    solicitud.estado_id = estado_cancelada.id
    solicitud.fecha_cierre = datetime.now(timezone.utc)
    if solicitud.tecnico_id:
        tecnico = await db.get(Tecnico, solicitud.tecnico_id)
        if tecnico:
            tecnico.disponibilidad = True
    cliente = await db.get(Cliente, solicitud.cliente_id)
    operador_ids = await _get_operador_user_ids(db)
    notify_ids = operador_ids + ([cliente.user_id] if cliente else [])
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_actual.nombre,
            estado_nuevo=estado_cancelada.nombre,
            observacion=payload.observacion,
            usuario_id=current_user.id,
        )
    )
    await _notify_users(
        db,
        notify_ids,
        "Solicitud cancelada",
        f"La solicitud #{solicitud.id} fue cancelada.",
        "SOLICITUD_CANCELADA",
    )
    await db.commit()

    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.get("/evidencias/{evidence_id:int}/archivo")
async def get_evidence_file(
    evidence_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    current_user = await _resolve_user_from_request(request, db)
    current_cliente_id, current_tecnico_id, current_taller_id = await _resolve_actor_ids(db, current_user)

    result = await db.execute(
        select(EvidenciaSolicitud).options(selectinload(EvidenciaSolicitud.solicitud)).where(EvidenciaSolicitud.id == evidence_id)
    )
    evidence = result.scalar_one_or_none()
    if not evidence or evidence.tipo == "TEXT":
        raise HTTPException(status_code=404, detail="Evidencia no encontrada")
    if not evidence.solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")

    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, evidence.solicitud)

    backend_root = Path(__file__).resolve().parents[2]
    storage_dir = EVIDENCE_STORAGE_DIR
    storage_dir.mkdir(parents=True, exist_ok=True)

    resolved_path: Path | None = None
    if evidence.archivo_url:
        candidate = (backend_root / evidence.archivo_url).resolve()
        if str(candidate).lower().startswith(str(backend_root.resolve()).lower()) and candidate.is_file():
            resolved_path = candidate
    if not resolved_path:
        suffix = Path(evidence.nombre_archivo or "").suffix
        candidates: list[Path] = []
        if evidence.solicitud_id:
            pattern = f"solicitud_{evidence.solicitud_id}_*{suffix}" if suffix else f"solicitud_{evidence.solicitud_id}_*"
            candidates.extend([item for item in storage_dir.glob(pattern) if item.is_file()])
        if candidates:
            resolved_path = sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0]

    if not resolved_path or not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="Archivo de evidencia no disponible")

    media_type = evidence.mime_type or mimetypes.guess_type(resolved_path.name)[0] or "application/octet-stream"
    if evidence.tipo == "IMAGE":
        return FileResponse(str(resolved_path), media_type=media_type)
    filename = evidence.nombre_archivo or resolved_path.name
    return FileResponse(str(resolved_path), media_type=media_type, filename=filename)


@router.get("/{solicitud_id:int}/evidencias", response_model=list[EvidenciaResponse])
async def list_request_evidence(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> list[EvidenciaResponse]:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    return [_evidence_to_response(item) for item in sorted(solicitud.evidencias, key=lambda item: item.fecha_creacion, reverse=True)]


@router.post("/{solicitud_id:int}/evidencias/texto", response_model=EvidenciaResponse, status_code=status.HTTP_201_CREATED)
async def add_text_evidence(
    solicitud_id: int,
    contenido_texto: str = Form(...),
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> EvidenciaResponse:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    evidence = EvidenciaSolicitud(
        solicitud_id=solicitud.id,
        usuario_id=current_user.id,
        tipo="TEXT",
        contenido_texto=contenido_texto.strip(),
    )
    merged_text = f"{solicitud.descripcion} {contenido_texto.strip()}".strip()
    normalized_text = merged_text.lower()
    if "carretera" in normalized_text:
        solicitud.es_carretera = True
    if any(keyword in normalized_text for keyword in ["inmovilizado", "no arranca"]):
        solicitud.condicion_vehiculo = "Vehículo inmovilizado"
    if any(keyword in normalized_text for keyword in ["choque", "colision", "colisión", "humo", "freno"]):
        solicitud.nivel_riesgo = max(solicitud.nivel_riesgo, 4)
    triage = analyze_incident(
        tipo_incidente=solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else "Incidente",
        descripcion=merged_text,
        es_carretera=solicitud.es_carretera,
        condicion_vehiculo=solicitud.condicion_vehiculo,
        nivel_riesgo=solicitud.nivel_riesgo,
    )
    solicitud.clasificacion_confianza = max(solicitud.clasificacion_confianza or 0, triage.confidence)
    solicitud.etiquetas_ia = _merge_ai_tags(solicitud.etiquetas_ia, triage.detected_tags)
    if triage.requires_manual_review:
        solicitud.requiere_revision_manual = True
    _apply_cost_estimate(solicitud)
    db.add(evidence)
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
            estado_nuevo=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
            observacion=f"Se adjuntó evidencia textual y se actualizó el costo estimado a {format_bs(solicitud.costo_estimado)}",
            usuario_id=current_user.id,
        )
    )
    await db.commit()
    await db.refresh(evidence)
    return _evidence_to_response(evidence)


@router.post("/{solicitud_id:int}/evidencias/archivo", response_model=EvidenciaResponse, status_code=status.HTTP_201_CREATED)
async def add_file_evidence(
    solicitud_id: int,
    archivo: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> EvidenciaResponse:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    if archivo.content_type not in ALLOWED_EVIDENCE_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido")
    content = await archivo.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="El archivo excede el tamaño máximo permitido")
    EVIDENCE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    extension = Path(archivo.filename or "evidencia").suffix
    target_path = EVIDENCE_STORAGE_DIR / f"solicitud_{solicitud.id}_{int(datetime.now(timezone.utc).timestamp())}{extension}"
    target_path.write_bytes(content)
    content_type = (archivo.content_type or "").lower()
    extension = Path(archivo.filename or "").suffix.lower()
    audio_extensions = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".webm"}
    evidence_type = "AUDIO" if content_type.startswith("audio/") or extension in audio_extensions else "IMAGE"
    evidence = EvidenciaSolicitud(
        solicitud_id=solicitud.id,
        usuario_id=current_user.id,
        tipo=evidence_type,
        nombre_archivo=archivo.filename,
        archivo_url=str(target_path.relative_to(Path(__file__).resolve().parents[2])),
        mime_type=archivo.content_type,
        tamano_bytes=len(content),
    )
    db.add(evidence)
    if evidence_type == "AUDIO":
        try:
            solicitud.transcripcion_audio_estado = "PROCESANDO"
            transcription = await transcribe_audio_file(
                archivo.filename or target_path.name,
                archivo.content_type,
                len(content),
                file_bytes=content,
            )
            normalized_transcript = transcription.transcript.lower()
            if "carretera" in normalized_transcript:
                solicitud.es_carretera = True
            if any(keyword in normalized_transcript for keyword in ["inmovilizado", "no arranca"]):
                solicitud.condicion_vehiculo = "Vehículo inmovilizado"
            if any(keyword in normalized_transcript for keyword in ["choque", "colision", "colisión", "humo", "freno"]):
                solicitud.nivel_riesgo = max(solicitud.nivel_riesgo, 4)
            audio_triage = analyze_incident(
                tipo_incidente=solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else "Incidente",
                descripcion=f"{solicitud.descripcion} {transcription.transcript}".strip(),
                es_carretera=solicitud.es_carretera,
                condicion_vehiculo=solicitud.condicion_vehiculo,
                nivel_riesgo=solicitud.nivel_riesgo,
            )
            solicitud.transcripcion_audio = transcription.transcript
            solicitud.transcripcion_audio_estado = "COMPLETADA"
            solicitud.transcripcion_audio_error = None
            solicitud.transcripcion_audio_actualizada_en = datetime.now(timezone.utc)
            solicitud.proveedor_ia = transcription.provider
            solicitud.resumen_ia = audio_triage.summary
            solicitud.clasificacion_confianza = max(solicitud.clasificacion_confianza or 0, transcription.confidence, audio_triage.confidence)
            solicitud.etiquetas_ia = _merge_ai_tags(solicitud.etiquetas_ia, audio_triage.detected_tags)
            if transcription.confidence < 0.65 or audio_triage.requires_manual_review:
                solicitud.requiere_revision_manual = True

            roles = set(get_role_names(current_user))
            if "CLIENTE" in roles and solicitud.transcripcion_audio:
                snippet = solicitud.transcripcion_audio.strip().replace("\n", " ")
                if len(snippet) > 260:
                    snippet = snippet[:260] + "..."
                notify_ids = await _get_operador_user_ids(db)
                notify_ids.extend(await _get_admin_user_ids(db))
                await _notify_users(
                    db,
                    notify_ids,
                    "Audio transcrito",
                    f"Solicitud #{solicitud.id}: {snippet}",
                    "AUDIO_TRANSCRITO",
                )
                db.add(
                    HistorialEvento(
                        solicitud_id=solicitud.id,
                        estado_anterior=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
                        estado_nuevo=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
                        observacion="Audio recibido y transcrito automáticamente para operación.",
                        usuario_id=current_user.id,
                    )
                )
        except Exception as exc:
            solicitud.transcripcion_audio_estado = "ERROR"
            solicitud.transcripcion_audio_error = str(exc)[:500]
            solicitud.transcripcion_audio_actualizada_en = datetime.now(timezone.utc)
    else:
        image_analysis = await analyze_image_file(
            archivo.filename or target_path.name,
            archivo.content_type,
            f"{solicitud.descripcion} {solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else ''}",
            file_bytes=content,
        )
        solicitud.resumen_ia = image_analysis.summary
        solicitud.proveedor_ia = image_analysis.provider
        solicitud.clasificacion_confianza = max(solicitud.clasificacion_confianza or 0, image_analysis.confidence)
        solicitud.etiquetas_ia = _merge_ai_tags(solicitud.etiquetas_ia, image_analysis.labels)
        if "choque" in image_analysis.labels or "motor" in image_analysis.labels:
            solicitud.nivel_riesgo = max(solicitud.nivel_riesgo, 4)
        if image_analysis.confidence < 0.65:
            solicitud.requiere_revision_manual = True
    _apply_cost_estimate(solicitud)
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
            estado_nuevo=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
            observacion=(
                f"Se adjuntó evidencia {evidence_type.lower()} y se actualizó el análisis IA y el costo estimado"
                if evidence_type in {"AUDIO", "IMAGE"}
                else f"Se adjuntó evidencia {evidence_type.lower()}"
            ),
            usuario_id=current_user.id,
        )
    )
    await db.commit()
    await db.refresh(evidence)
    return _evidence_to_response(evidence)


@router.put("/{solicitud_id:int}/trabajo-finalizado", response_model=SolicitudResponse)
async def finalize_request_work(
    solicitud_id: int,
    payload: SolicitudTrabajoFinalizadoRequest,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    roles = get_role_names(current_user)
    if "TECNICO" not in roles or solicitud.tecnico_id != current_tecnico_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el técnico asignado puede cerrar el trabajo")
    estado_actual = solicitud.estado.nombre if solicitud.estado else ""
    if estado_actual != "EN_ATENCION":
        raise HTTPException(status_code=400, detail="La solicitud debe estar en atención para cerrar el trabajo técnico")
    if solicitud.trabajo_terminado:
        raise HTTPException(status_code=400, detail="El trabajo técnico ya fue registrado como finalizado")

    solicitud.trabajo_terminado = True
    solicitud.trabajo_terminado_en = datetime.now(timezone.utc)
    solicitud.trabajo_terminado_observacion = payload.observacion.strip()
    solicitud.costo_final = round(payload.costo_final, 2)
    solicitud.moneda_costo = "BOB"

    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_actual,
            estado_nuevo=estado_actual,
            observacion=f"Trabajo realizado. Costo final {format_bs(solicitud.costo_final)}. {payload.observacion.strip()}",
            usuario_id=current_user.id,
        )
    )

    notify_ids = await _get_operador_user_ids(db)
    if solicitud.cliente and solicitud.cliente.user_id:
        notify_ids.append(solicitud.cliente.user_id)
    await _notify_users(
        db,
        notify_ids,
        "Trabajo finalizado",
        f"El técnico cerró el trabajo de la solicitud #{solicitud.id} con costo final {format_bs(solicitud.costo_final)}.",
        "TRABAJO_FINALIZADO",
    )
    await db.commit()

    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.get("/{solicitud_id:int}/pagos", response_model=list[PagoResponse])
async def list_request_payments(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> list[PagoSolicitud]:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    return sorted(solicitud.pagos, key=lambda item: item.fecha_creacion, reverse=True)


@router.post("/{solicitud_id:int}/pago", response_model=PagoResponse, status_code=status.HTTP_201_CREATED)
async def create_request_payment(
    solicitud_id: int,
    payload: PagoCreate,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    db: AsyncSession = Depends(get_db),
) -> PagoSolicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    roles = get_role_names(current_user)
    if "CLIENTE" not in roles or solicitud.cliente_id != current_cliente_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el cliente propietario puede pagar esta solicitud")
    estado_actual = solicitud.estado.nombre if solicitud.estado else ""
    if estado_actual not in {"EN_ATENCION", "COMPLETADA"}:
        raise HTTPException(status_code=400, detail="La solicitud aún no está lista para registrar el pago")
    if solicitud.cliente_aprobada is False:
        raise HTTPException(status_code=400, detail="Primero debes aprobar la propuesta antes de registrar el pago")
    if not solicitud.trabajo_terminado or solicitud.costo_final is None:
        raise HTTPException(status_code=400, detail="El técnico aún debe registrar el trabajo realizado y el costo final en Bs")
    existing_paid = next((item for item in solicitud.pagos if item.estado == "PAGADO"), None)
    if existing_paid:
        raise HTTPException(status_code=400, detail="La solicitud ya tiene un pago confirmado")
    monto_total = _resolve_payment_amount(solicitud, payload.monto_total)
    breakdown = calculate_payment_breakdown(monto_total)
    estado_pago = "PAGADO" if payload.confirmar_pago else "REGISTRADO"
    pago = next(
        (
            item
            for item in sorted(solicitud.pagos, key=lambda item: item.fecha_creacion, reverse=True)
            if item.estado in {"PENDIENTE", "REGISTRADO"}
        ),
        None,
    )
    if pago is None:
        pago = PagoSolicitud(
            solicitud_id=solicitud.id,
            cliente_id=solicitud.cliente_id,
            taller_id=solicitud.taller_id,
            monto_total=breakdown.total,
            monto_comision=breakdown.commission,
            monto_taller=breakdown.workshop_amount,
            metodo_pago=payload.metodo_pago,
            estado=estado_pago,
            referencia_externa=payload.referencia_externa,
            observacion=payload.observacion,
            fecha_pago=datetime.now(timezone.utc) if payload.confirmar_pago else None,
        )
        db.add(pago)
    else:
        pago.monto_total = breakdown.total
        pago.monto_comision = breakdown.commission
        pago.monto_taller = breakdown.workshop_amount
        pago.metodo_pago = payload.metodo_pago
        pago.estado = estado_pago
        pago.referencia_externa = payload.referencia_externa
        pago.observacion = payload.observacion
        pago.fecha_pago = datetime.now(timezone.utc) if payload.confirmar_pago else None
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_actual or "SIN_ESTADO",
            estado_nuevo=estado_actual or "SIN_ESTADO",
            observacion=(
                f"Pago confirmado por {format_bs(breakdown.total)} con comisión {format_bs(breakdown.commission)}"
                if payload.confirmar_pago
                else f"Intención de pago registrada por {format_bs(breakdown.total)} mediante {payload.metodo_pago}"
            ),
            usuario_id=current_user.id,
        )
    )
    if payload.confirmar_pago and estado_actual != "COMPLETADA":
        estado_completada = await _get_estado_por_nombre(db, "COMPLETADA")
        solicitud.estado_id = estado_completada.id
        solicitud.fecha_cierre = datetime.now(timezone.utc)
        if solicitud.tecnico_id:
            tecnico = await db.get(Tecnico, solicitud.tecnico_id)
            if tecnico:
                tecnico.disponibilidad = True
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual or "SIN_ESTADO",
                estado_nuevo=estado_completada.nombre,
                observacion="Solicitud completada automaticamente tras la confirmacion del pago final.",
                usuario_id=current_user.id,
            )
        )
    notify_ids = [current_user.id]
    if solicitud.taller and solicitud.taller.user_id:
        notify_ids.append(solicitud.taller.user_id)
    notify_ids.extend(await _get_operador_user_ids(db))
    await _notify_users(
        db,
        notify_ids,
        "Pago confirmado" if payload.confirmar_pago else "Pago registrado",
        (
            f"Se confirmó el pago de la solicitud #{solicitud.id} por {format_bs(breakdown.total)}. Comisión plataforma: {format_bs(breakdown.commission)}."
            if payload.confirmar_pago
            else f"El cliente registró intención de pago para la solicitud #{solicitud.id} por {format_bs(breakdown.total)}."
        ),
        "PAGO_CONFIRMADO" if payload.confirmar_pago else "PAGO_REGISTRADO",
    )
    await db.commit()
    await db.refresh(pago)
    return pago


@router.get("/{solicitud_id:int}/disputas", response_model=list[DisputaResponse])
async def list_request_disputes(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> list[DisputaSolicitud]:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    return sorted(solicitud.disputas, key=lambda item: item.fecha_creacion, reverse=True)


@router.get("/trabajos", response_model=TrabajoRealizadoListResponse)
async def list_completed_jobs(
    desde: str | None = Query(default=None),
    hasta: str | None = Query(default=None),
    tecnico_id: int | None = Query(default=None),
    taller_id: int | None = Query(default=None),
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR", "TALLER")),
    db: AsyncSession = Depends(get_db),
) -> TrabajoRealizadoListResponse:
    return await _fetch_trabajos_realizados(db, desde, hasta, tecnico_id, taller_id)


@router.get("/trabajos.pdf")
async def export_completed_jobs_pdf(
    request: Request,
    desde: str | None = Query(default=None),
    hasta: str | None = Query(default=None),
    tecnico_id: int | None = Query(default=None),
    taller_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await _resolve_user_from_request(request, db)
    roles = set(get_role_names(user))
    if not roles.intersection({"ADMINISTRADOR", "OPERADOR", "TALLER"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")

    data = await _fetch_trabajos_realizados(db, desde, hasta, tecnico_id, taller_id)
    resumen = data.resumen

    header = "ID | Fecha | Cliente | Taller | Tecnico | Total | Comision | Taller"
    lines = [
        f"Filtros: desde={desde or '-'} hasta={hasta or '-'} tecnico_id={tecnico_id or '-'} taller_id={taller_id or '-'}",
        f"Cantidad: {resumen.cantidad_trabajos}",
        f"Total facturado: {format_bs(resumen.total_facturado)}",
        f"Total comision: {format_bs(resumen.total_comision)}",
        f"Total taller: {format_bs(resumen.total_taller)}",
        f"Promedio: {format_bs(resumen.promedio_por_trabajo)}",
        "",
        header,
    ]
    for item in data.items:
        fecha = item.fecha_cierre.strftime("%Y-%m-%d")
        lines.append(
            f"{item.solicitud_id} | {fecha} | {item.cliente} | {item.taller} | {item.tecnico} | {format_bs(item.monto_total)} | {format_bs(item.monto_comision)} | {format_bs(item.monto_taller)}"
        )

    pdf_bytes = build_invoice_pdf(title="Reporte - Trabajos realizados", lines=lines)
    filename = "trabajos_realizados.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/trabajos.csv")
async def export_completed_jobs_csv(
    request: Request,
    desde: str | None = Query(default=None),
    hasta: str | None = Query(default=None),
    tecnico_id: int | None = Query(default=None),
    taller_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await _resolve_user_from_request(request, db)
    roles = set(get_role_names(user))
    if not roles.intersection({"ADMINISTRADOR", "OPERADOR", "TALLER"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")

    data = await _fetch_trabajos_realizados(db, desde, hasta, tecnico_id, taller_id)

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "solicitud_id",
            "fecha_cierre",
            "cliente",
            "taller",
            "tecnico",
            "tipo_incidente",
            "costo_estimado",
            "costo_final",
            "monto_total",
            "monto_comision",
            "monto_taller",
            "metodo_pago",
            "estado_pago",
        ]
    )
    for item in data.items:
        writer.writerow(
            [
                item.solicitud_id,
                item.fecha_cierre.isoformat(),
                item.cliente,
                item.taller,
                item.tecnico,
                item.tipo_incidente,
                item.costo_estimado,
                item.costo_final,
                item.monto_total,
                item.monto_comision,
                item.monto_taller,
                item.metodo_pago,
                item.estado_pago,
            ]
        )
    writer.writerow([])
    writer.writerow(["cantidad_trabajos", data.resumen.cantidad_trabajos])
    writer.writerow(["total_facturado", data.resumen.total_facturado])
    writer.writerow(["total_comision", data.resumen.total_comision])
    writer.writerow(["total_taller", data.resumen.total_taller])
    writer.writerow(["promedio_por_trabajo", data.resumen.promedio_por_trabajo])

    filename = "trabajos_realizados.csv"
    return Response(
        content=buffer.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/{solicitud_id:int}/audio/transcribir", response_model=SolicitudResponse)
async def retry_audio_transcription(
    solicitud_id: int,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")

    audio_evidences = [item for item in solicitud.evidencias if item.tipo == "AUDIO"]
    if not audio_evidences:
        raise HTTPException(status_code=400, detail="No hay evidencia de audio para transcribir")
    latest_audio = sorted(audio_evidences, key=lambda item: item.fecha_creacion, reverse=True)[0]

    solicitud.transcripcion_audio_estado = "PROCESANDO"
    solicitud.transcripcion_audio_error = None
    solicitud.transcripcion_audio_actualizada_en = datetime.now(timezone.utc)

    try:
        file_bytes: bytes | None = None
        if latest_audio.archivo_url:
            backend_root = Path(__file__).resolve().parents[2]
            candidate = (backend_root / latest_audio.archivo_url).resolve()
            if str(candidate).lower().startswith(str(backend_root.resolve()).lower()) and candidate.is_file():
                file_bytes = candidate.read_bytes()
        transcription = await transcribe_audio_file(
            latest_audio.nombre_archivo or Path(latest_audio.archivo_url or "").name,
            latest_audio.mime_type,
            latest_audio.tamano_bytes or 0,
            file_bytes=file_bytes,
        )
        solicitud.transcripcion_audio = transcription.transcript
        solicitud.transcripcion_audio_estado = "COMPLETADA"
        solicitud.transcripcion_audio_actualizada_en = datetime.now(timezone.utc)
        solicitud.proveedor_ia = transcription.provider
        _apply_cost_estimate(solicitud)
    except Exception as exc:
        solicitud.transcripcion_audio_estado = "ERROR"
        solicitud.transcripcion_audio_error = str(exc)[:500]
        solicitud.transcripcion_audio_actualizada_en = datetime.now(timezone.utc)

    await db.commit()
    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.get("/{solicitud_id:int}/factura.pdf")
async def download_invoice_pdf(
    solicitud_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await _resolve_user_from_request(request, db)
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    cliente_id = await db.scalar(select(Cliente.id).where(Cliente.user_id == user.id))
    tecnico_id = await db.scalar(select(Tecnico.id).where(Tecnico.user_id == user.id))
    taller_id = await db.scalar(select(Taller.id).where(Taller.user_id == user.id))
    validate_request_access(user, cliente_id, tecnico_id, taller_id, solicitud)

    paid = _get_latest_paid_payment(solicitud)
    if not paid:
        raise HTTPException(status_code=400, detail="No hay un pago confirmado para generar la factura")
    if solicitud.costo_final is None:
        raise HTTPException(status_code=400, detail="No hay un costo final registrado para generar la factura")

    cliente_nombre = solicitud.cliente.nombre if solicitud.cliente else "Cliente"
    placa = solicitud.vehiculo.placa if solicitud.vehiculo else "N/A"
    taller_nombre = solicitud.taller.nombre if solicitud.taller else "Sin taller"
    tecnico_nombre = solicitud.tecnico.nombre if solicitud.tecnico else "Sin tecnico"
    estado_nombre = solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO"
    fecha = paid.fecha_pago or paid.fecha_creacion
    fecha_str = fecha.strftime("%Y-%m-%d %H:%M") if fecha else ""

    pdf_bytes = build_invoice_pdf(
        title=f"Factura - Solicitud #{solicitud.id}",
        lines=[
            f"Fecha: {fecha_str}",
            f"Cliente: {cliente_nombre}",
            f"Vehiculo: {placa}",
            f"Tipo: {solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else 'Incidente'}",
            f"Taller: {taller_nombre}",
            f"Tecnico: {tecnico_nombre}",
            f"Estado: {estado_nombre}",
            "",
            f"Costo estimado IA: {format_bs(solicitud.costo_estimado)}",
            f"Costo final tecnico: {format_bs(solicitud.costo_final)}",
            "",
            f"Pago confirmado: {format_bs(paid.monto_total)}",
            f"Comision plataforma: {format_bs(paid.monto_comision)}",
            f"Monto taller: {format_bs(paid.monto_taller)}",
            f"Metodo: {paid.metodo_pago}",
            f"Referencia: {paid.referencia_externa or 'N/A'}",
        ],
    )
    filename = f"factura_solicitud_{solicitud.id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/{solicitud_id:int}/disputas", response_model=DisputaResponse, status_code=status.HTTP_201_CREATED)
async def create_request_dispute(
    solicitud_id: int,
    payload: DisputaCreate,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> DisputaSolicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)
    disputa = DisputaSolicitud(
        solicitud_id=solicitud.id,
        usuario_id=current_user.id,
        motivo=payload.motivo,
        detalle=payload.detalle,
        estado="ABIERTA",
    )
    db.add(disputa)
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
            estado_nuevo=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
            observacion=f"Se abrió disputa: {payload.motivo}",
            usuario_id=current_user.id,
        )
    )
    operator_ids = await _get_operador_user_ids(db)
    await _notify_users(
        db,
        operator_ids,
        "Nueva disputa",
        f"La solicitud #{solicitud.id} recibió una disputa por motivo: {payload.motivo}.",
        "DISPUTA_ABIERTA",
    )
    await db.commit()
    await db.refresh(disputa)
    return disputa


@router.put("/disputas/{disputa_id}/resolver", response_model=DisputaResponse)
async def resolve_request_dispute(
    disputa_id: int,
    payload: DisputaResolverRequest,
    current_user: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> DisputaSolicitud:
    disputa = await db.get(DisputaSolicitud, disputa_id)
    if not disputa:
        raise HTTPException(status_code=404, detail="Disputa no encontrada")
    disputa.estado = "RESUELTA"
    disputa.resolucion = payload.resolucion
    disputa.fecha_resolucion = datetime.now(timezone.utc)
    solicitud = await _load_request_with_relations(db, disputa.solicitud_id)
    if solicitud:
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
                estado_nuevo=solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO",
                observacion="Disputa resuelta por operación",
                usuario_id=current_user.id,
            )
        )
        notify_ids = [disputa.usuario_id]
        await _notify_users(
            db,
            notify_ids,
            "Disputa resuelta",
            f"La disputa de la solicitud #{solicitud.id} fue resuelta.",
            "DISPUTA_RESUELTA",
        )
    await db.commit()
    await db.refresh(disputa)
    return disputa


@router.put("/{solicitud_id:int}/estado", response_model=SolicitudResponse)
async def update_request_status(
    solicitud_id: int,
    payload: SolicitudEstadoUpdate,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await db.get(Solicitud, solicitud_id)
    nuevo_estado = await db.get(EstadoSolicitud, payload.estado_id)
    if not solicitud or not nuevo_estado:
        raise HTTPException(status_code=404, detail="Solicitud o estado no encontrado")
    roles = get_role_names(current_user)
    if not roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        if "TECNICO" not in roles or solicitud.tecnico_id != current_tecnico_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes actualizar esta solicitud")

    estado_actual = await db.get(EstadoSolicitud, solicitud.estado_id)
    if not estado_actual:
        raise HTTPException(status_code=404, detail="Estado actual no encontrado")
    if not can_transition_request(estado_actual.nombre, nuevo_estado.nombre, roles):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se permite pasar de {estado_actual.nombre} a {nuevo_estado.nombre}",
        )
    if nuevo_estado.nombre == "COMPLETADA":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La solicitud se completa automáticamente solo después de confirmar el pago final.",
        )
    solicitud.estado_id = nuevo_estado.id

    if nuevo_estado.nombre == "EN_ATENCION":
        solicitud.fecha_atencion = datetime.now(timezone.utc)
    if nuevo_estado.nombre in {"COMPLETADA", "CANCELADA"}:
        solicitud.fecha_cierre = datetime.now(timezone.utc)
        if solicitud.tecnico_id:
            tecnico = await db.get(Tecnico, solicitud.tecnico_id)
            if tecnico:
                tecnico.disponibilidad = True

    cliente = await db.get(Cliente, solicitud.cliente_id)
    usuario_id = cliente.user_id if cliente else None
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_actual.nombre if estado_actual else "SIN_ESTADO",
            estado_nuevo=nuevo_estado.nombre,
            observacion=payload.observacion,
            usuario_id=current_user.id,
        )
    )
    if usuario_id:
        db.add(
            Notificacion(
                usuario_id=usuario_id,
                titulo="Actualización de solicitud",
                mensaje=f"Tu solicitud #{solicitud.id} cambió a {nuevo_estado.nombre}.",
                tipo="CAMBIO_ESTADO",
            )
        )

    await db.commit()

    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result
