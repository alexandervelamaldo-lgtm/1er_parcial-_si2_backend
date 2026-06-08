import csv
import colorsys
import json
import logging
import mimetypes
import time
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import desc, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, get_tenant_sessionmaker
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
from app.models.servicios_taller_demanda import ServicioTallerDemanda
from app.services.workshop_tenant_routing import resolve_workshop_tenant_key
from app.utils.auth import hash_password
from app.schemas.gestion_solicitudes.disputas import DisputaCreate, DisputaResolverRequest, DisputaResponse
from app.schemas.gestion_solicitudes.evidencias import EvidenciaResponse
from app.schemas.gestion_solicitudes.historial_eventos import HistorialEventoResponse
from app.schemas.gestion_solicitudes.solicitudes import (
    EstadoSolicitudOptionResponse,
    SolicitudAsignar,
    SolicitudActualizarRutaRequest,
    SolicitudCancelarRequest,
    SolicitudCandidatosResponse,
    SolicitudCreate,
    SolicitudDetalleResponse,
    SolicitudSeleccionTallerRequest,
    SolicitudEstadoUpdate,
    SolicitudRespuestaClienteRequest,
    SolicitudRevisionManualRequest,
    SolicitudResponderAsignacionRequest,
    SolicitudResponse,
    SolicitudSeguimientoResponse,
    SolicitudTrabajoFinalizadoRequest,
    TallerConPresupuestoResponse,
    TalleresConPresupuestoResponse,
    TallerPresupuestoBreakdown,
    TecnicoCandidatoResponse,
    TrabajoRealizadoItemResponse,
    TrabajoRealizadoListResponse,
    TrabajoRealizadoResumenResponse,
)
from app.schemas.gestion_solicitudes.tipos_incidente import TipoIncidenteResponse
from app.schemas.gestion_operativa_web.talleres import TallerResponse
from app.schemas.pagos_facturacion.pagos import PagoCreate, PagoResponse
from app.config import get_settings
from app.services.gestion_operativa_web.notificacion_service import enviar_notificacion_push
from app.services.gestion_operativa_web.demanda_matching_service import calcular_match_tecnico
from app.services.gestion_operativa_web.taller_presupuesto_service import (
    calcular_presupuesto_estimado,
    descuento_por_marca_asociada,
)
from app.services.gestion_operativa_web.web_push_service import enviar_web_push
from app.services.inteligencia_automatizacion.multimodal_ai_service import (
    analyze_image_file,
    persist_image_ai_outcome,
    transcribe_audio_file,
)
from app.services.inteligencia_automatizacion.prioridad_service import calcular_prioridad
from app.services.inteligencia_automatizacion.triage_service import (
    analyze_incident,
    estimate_repair_cost,
    infer_risk_level,
)
from app.services.mapa.mapbox_directions_service import MapboxRoute, route_driving
from app.services.mapa.travel_time_policy import estimate_eta_minutes, estimate_eta_range_minutes
from app.services.pagos_facturacion.invoice_pdf_service import build_invoice_pdf, format_bs
from app.services.pagos_facturacion.payment_service import calculate_payment_breakdown
from app.services.realtime_hub import hub as _realtime_hub
from app.routers.gestion_operativa_web.kpis import invalidate_kpi_cache_for_tenant
from jose import JWTError
from app.models.enums import CategoriaDano, EstadoSolicitudEnum, resolve_categoria_diagnostico, try_parse_categoria_dano
from app.utils.auth import decode_token
from app.utils.geo import calcular_distancia_km
from app.models.notification_preferences import UserNotificationPreferences
from app.models.web_push_subscriptions import WebPushSubscription


router = APIRouter(prefix="/solicitudes", tags=["Solicitudes"])
settings = get_settings()
logger = logging.getLogger(__name__)
KNOWN_REQUEST_STATES = {state.value for state in EstadoSolicitudEnum}


# #region debug-point C:web-push-dispatch-report
def _debug_report(hypothesis_id: str, location: str, msg: str, data: dict) -> None:
    _p = ".dbg/mobile-request-push.env"
    _u = None
    _s = "mobile-request-push"
    try:
        with open(_p, encoding="utf-8") as f:
            c = f.read()
        _u = next((line.split("=", 1)[1] for line in c.splitlines() if line.startswith("DEBUG_SERVER_URL=")), None)
        _s = next((line.split("=", 1)[1] for line in c.splitlines() if line.startswith("DEBUG_SESSION_ID=")), _s)
    except Exception:
        pass
    if not _u:
        return
    try:
        payload = {
            "sessionId": _s,
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "msg": f"[DEBUG] {msg}",
            "data": data,
            "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        urllib.request.urlopen(
            urllib.request.Request(
                _u,
                data=json.dumps(payload, default=str).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=0.3,
        ).read()
    except Exception:
        pass
# #endregion


async def _broadcast_state_change(
    tenant: str,
    solicitud_id: int,
    estado: str,
    taller_id: int | None = None,
    tecnico_id: int | None = None,
) -> None:
    """Best-effort: broadcast state change to WebSocket clients. Never raises."""
    try:
        await invalidate_kpi_cache_for_tenant(tenant)
        await _realtime_hub.broadcast_solicitud_update(
            tenant,
            solicitud_id=solicitud_id,
            estado=estado,
            taller_id=taller_id,
            tecnico_id=tecnico_id,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        await _realtime_hub.broadcast_kpi_refresh(tenant)
    except Exception:
        pass

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


class _TranscriptionUnavailable(Exception):
    """Señal interna: ningún proveedor real transcribió el audio. El estado
    ERROR honesto ya se registró en la solicitud antes de lanzarse."""


TRANSICIONES_OPERATIVAS = {
    # REGISTRADA puede pasar a:
    #  - ASIGNADA           → flujo legacy (operador asigna manualmente)
    #  - PROPUESTA_TALLER   → flujo nuevo (cliente eligió taller desde la app)
    #  - CANCELADA          → cliente cancela
    "REGISTRADA":       {"ASIGNADA", "PROPUESTA_TALLER", "CANCELADA"},
    # PROPUESTA_TALLER → el taller decide:
    #  - ASIGNADA           → taller aceptó (luego pasa a EN_CAMINO)
    #  - RECHAZADA_TALLER   → taller rechazó (cliente debe re-elegir)
    #  - CANCELADA          → cliente se arrepiente antes de que responda
    "PROPUESTA_TALLER": {"ASIGNADA", "RECHAZADA_TALLER", "CANCELADA"},
    # RECHAZADA_TALLER → cliente debe re-elegir o cancelar
    "RECHAZADA_TALLER": {"PROPUESTA_TALLER", "CANCELADA"},
    "ASIGNADA":         {"EN_CAMINO", "CANCELADA"},
    "EN_CAMINO":        {"EN_ATENCION", "CANCELADA"},
    "EN_ATENCION":      {"COMPLETADA", "CANCELADA"},
}


async def _get_estado_por_nombre(db: AsyncSession, nombre: str) -> EstadoSolicitud:
    estado = await db.scalar(select(EstadoSolicitud).where(EstadoSolicitud.nombre == nombre))
    if estado:
        return estado
    if nombre in KNOWN_REQUEST_STATES:
        # Tenant restored from partial backup or incomplete seed: recreate the
        # canonical state instead of failing mobile/web request creation.
        estado = EstadoSolicitud(nombre=nombre)
        db.add(estado)
        await db.flush()
        return estado
    raise HTTPException(status_code=404, detail=f"Estado {nombre} no encontrado")


async def _ensure_known_request_states(db: AsyncSession) -> None:
    result = await db.execute(select(EstadoSolicitud.nombre))
    existing = {name for name in result.scalars().all() if name}
    missing = [name for name in KNOWN_REQUEST_STATES if name not in existing]
    if not missing:
        return
    for nombre in missing:
        db.add(EstadoSolicitud(nombre=nombre))
    await db.flush()


async def _get_operador_user_ids(db: AsyncSession) -> list[int]:
    result = await db.execute(select(Operador.user_id))
    return list(result.scalars().all())


async def _get_admin_user_ids(db: AsyncSession) -> list[int]:
    result = await db.execute(
        select(User.id).join(User.roles).where(Role.name.in_(["ADMINISTRADOR", "ADMIN_TENANT"]))
    )
    return list(result.scalars().all())
def tracking_route_color(solicitud_id: int) -> str:
    hue = ((solicitud_id * 47) % 360) / 360
    red, green, blue = colorsys.hls_to_rgb(hue, 0.48, 0.72)
    return f"#{int(red * 255):02X}{int(green * 255):02X}{int(blue * 255):02X}"


def can_transition_request(current_state: str, new_state: str, roles: set[str]) -> bool:
    """
    Decide if a user with [roles] can move a request from [current_state] to
    [new_state]. The matrix below is the only place in the codebase that
    encodes the business state machine — every endpoint that mutates a
    request's state must call this first.

    Role permissions (additive — having any of these grants the right):

      ADMINISTRADOR / OPERADOR
          Full reign over the legacy transitions in TRANSICIONES_OPERATIVAS
          (including REGISTRADA → ASIGNADA for "Modo emergencia" / soporte).

      CLIENTE
          - REGISTRADA → PROPUESTA_TALLER    (eligió taller en la app)
          - RECHAZADA_TALLER → PROPUESTA_TALLER (re-eligió tras un rechazo)
          - Cualquier estado activo → CANCELADA  (puede arrepentirse)

      TALLER
          - PROPUESTA_TALLER → ASIGNADA       (aceptó la propuesta)
          - PROPUESTA_TALLER → RECHAZADA_TALLER (rechazó)
          - ASIGNADA → EN_CAMINO              (ya salió)
          - EN_CAMINO → EN_ATENCION           (llegó al incidente, sin técnico)
          - EN_ATENCION → COMPLETADA          (cerró el servicio, sin técnico)

      TECNICO
          - ASIGNADA → EN_CAMINO              (cuando el técnico es el ejecutor)
          - EN_CAMINO → EN_ATENCION
          - EN_ATENCION → COMPLETADA
    """
    if current_state == new_state:
        return True
    if current_state in ESTADOS_FINALES:
        return False
    allowed = TRANSICIONES_OPERATIVAS.get(current_state, set())

    # Admin / operador: alcance pleno sobre transiciones del catálogo.
    if roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        return new_state in allowed

    # Cliente: flujo cliente↔taller-directo.
    # Cancela SOLO antes de que el taller acepte (PROPUESTA_TALLER es
    # cancelable, ASIGNADA o EN_CAMINO ya no — debe ir a soporte).
    if "CLIENTE" in roles:
        if current_state == "REGISTRADA"        and new_state == "PROPUESTA_TALLER":
            return True
        if current_state == "RECHAZADA_TALLER"  and new_state == "PROPUESTA_TALLER":
            return True
        if new_state == "CANCELADA" and current_state in {
            "REGISTRADA", "PROPUESTA_TALLER", "RECHAZADA_TALLER"
        }:
            return True

    # Taller: acepta / rechaza / arranca camino / atiende sin técnico.
    if "TALLER" in roles:
        if current_state == "PROPUESTA_TALLER"  and new_state in {"ASIGNADA", "RECHAZADA_TALLER"}:
            return True
        if current_state == "ASIGNADA"          and new_state == "EN_CAMINO":
            return True
        if current_state == "EN_CAMINO"         and new_state == "EN_ATENCION":
            return True
        if current_state == "EN_ATENCION"       and new_state == "COMPLETADA":
            return True

    # Técnico: ejecución del servicio.
    if "TECNICO" in roles:
        if current_state == "ASIGNADA"   and new_state == "EN_CAMINO":
            return True
        if current_state == "EN_CAMINO"  and new_state == "EN_ATENCION":
            return True
        if current_state == "EN_ATENCION" and new_state == "COMPLETADA":
            return True

    return False


async def _load_request_with_relations(db: AsyncSession, solicitud_id: int) -> Solicitud | None:
    result = await db.execute(
        select(Solicitud)
        .options(
            selectinload(Solicitud.estado),
            selectinload(Solicitud.tipo_incidente),
            selectinload(Solicitud.cliente).selectinload(Cliente.user),
            selectinload(Solicitud.vehiculo),
            selectinload(Solicitud.servicio_demanda),
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


async def _resolve_user_role_ids_in_session(
    session: AsyncSession, user_email: str
) -> tuple[int | None, int | None, int | None, int | None]:
    """Resuelve (user_id, cliente_id, tecnico_id, taller_id) del usuario
    —identificado por email— DENTRO del tenant de ``session``.

    Las identidades son por-tenant (cada tenant tiene su propia secuencia de ids),
    así que el ``current_user.id`` / ``current_cliente_id`` inyectados (resueltos
    contra el tenant de la request) NO sirven para una solicitud que vive en OTRO
    tenant: usarlos al escribir (p.ej. ``HistorialEvento.usuario_id``) rompería la
    FK. Aquí recalculamos las identidades en el tenant correcto. Cualquiera puede
    ser None si el usuario no existe / no tiene ese rol en ese tenant.
    """
    user = await session.scalar(select(User).where(User.email == user_email))
    if user is None:
        return None, None, None, None
    cliente_id = await session.scalar(select(Cliente.id).where(Cliente.user_id == user.id))
    tecnico_id = await session.scalar(select(Tecnico.id).where(Tecnico.user_id == user.id))
    taller_id = await session.scalar(select(Taller.id).where(Taller.user_id == user.id))
    return user.id, cliente_id, tecnico_id, taller_id


@asynccontextmanager
async def _open_solicitud_session(
    db: AsyncSession,
    solicitud_id: int,
    current_user: User,
    current_cliente_id: int | None,
    current_tecnico_id: int | None,
    current_taller_id: int | None,
) -> AsyncIterator[
    tuple[Solicitud | None, AsyncSession, int, int | None, int | None, int | None]
]:
    """Localiza la solicitud aunque viva en OTRO tenant (enrutado por categoría).

    Un CLIENTE crea su solicitud en el tenant del servicio (gomería→llaneros,
    choque→chapa_pintura, …) pero LEE con el tenant de su login (default). Sin
    esto, ``GET /solicitudes/{id}/...`` busca en el tenant equivocado y responde
    404 aunque la solicitud exista. Este context manager cede la tupla
    ``(solicitud, session, usuario_id, cliente_id, tecnico_id, taller_id)`` donde
    ``session`` y los ids son los del tenant DONDE VIVE la solicitud (úsalos para
    validar acceso y para escribir, p.ej. ``HistorialEvento.usuario_id``):

      1. Fast-path: intenta cargar la solicitud en el tenant actual (``db``). Si
         está, cede ``(solicitud, db, current_user.id, ids_inyectados)`` sin abrir
         sesión extra.
      2. Si no está, recorre los demás tenants provisionados. En cada uno resuelve
         la identidad del usuario por email y comprueba pertenencia (la solicitud
         es suya como cliente/técnico/taller, o el usuario es ADMIN/OPERADOR). Si
         coincide, cede los ids DE ESE tenant; la sesión se cierra al salir del
         ``async with``.
      3. Si no aparece en ningún tenant, cede ``(None, db, current_user.id,
         ids_inyectados)`` y el caller responde 404.

    La comprobación de pertenencia por email evita colisiones de id entre tenants
    (id=3 puede existir en varios): sólo se devuelve la solicitud que de verdad
    pertenece a este usuario.
    """
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if solicitud is not None:
        yield solicitud, db, current_user.id, current_cliente_id, current_tecnico_id, current_taller_id
        return

    roles = get_role_names(current_user)
    is_privileged = _has_cross_tenant_request_visibility(roles)
    current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            continue
        tenant_sessionmaker = get_tenant_sessionmaker(tenant)
        async with tenant_sessionmaker() as session:
            # Que la sesión cargue su tenant_key como lo hace get_db: el código
            # aguas abajo (broadcasts, notificaciones) lo lee de session.info.
            session.info["tenant_key"] = tenant
            usuario_id, cliente_id, tecnico_id, taller_id = await _resolve_user_role_ids_in_session(
                session, current_user.email
            )
            solicitud = await _load_request_with_relations(session, solicitud_id)
            if solicitud is None:
                continue
            owned = (
                is_privileged
                or ("CLIENTE" in roles and cliente_id is not None and cliente_id == solicitud.cliente_id)
                or ("TECNICO" in roles and tecnico_id is not None and tecnico_id == solicitud.tecnico_id)
                or ("TALLER" in roles and taller_id is not None and taller_id == solicitud.taller_id)
            )
            if owned:
                # usuario_id no debería ser None aquí (el usuario se resolvió por
                # email para validar pertenencia); fallback defensivo a current_user.id.
                yield solicitud, session, (usuario_id or current_user.id), cliente_id, tecnico_id, taller_id
                return

    yield None, db, current_user.id, current_cliente_id, current_tecnico_id, current_taller_id


async def _fetch_client_requests_in_session(
    session: AsyncSession,
    *,
    user_email: str,
    diagnostico_categoria: str | None = None,
    only_active: bool = False,
) -> list[Solicitud]:
    query = (
        select(Solicitud)
        .join(Cliente, Cliente.id == Solicitud.cliente_id)
        .join(User, User.id == Cliente.user_id)
        .options(
            selectinload(Solicitud.estado),
            selectinload(Solicitud.tipo_incidente),
            selectinload(Solicitud.servicio_demanda),
            selectinload(Solicitud.vehiculo),
            selectinload(Solicitud.evidencias),
        )
        .where(User.email == user_email)
        .order_by(desc(Solicitud.fecha_solicitud))
    )
    if diagnostico_categoria is not None:
        query = query.where(Solicitud.categoria_dano == diagnostico_categoria)
    if only_active:
        query = query.join(EstadoSolicitud).where(EstadoSolicitud.nombre.not_in(["COMPLETADA", "CANCELADA"]))
    result = await session.execute(query)
    return list(result.scalars().all())


async def _list_client_requests_across_tenants(
    *,
    db: AsyncSession,
    current_user: User,
    diagnostico_categoria: str | None = None,
    only_active: bool = False,
) -> list[SolicitudResponse]:
    current_tenant = db.info.get("tenant_key", settings.default_tenant or "default")
    responses: list[SolicitudResponse] = []
    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            session = db
            solicitudes = await _fetch_client_requests_in_session(
                session,
                user_email=current_user.email,
                diagnostico_categoria=diagnostico_categoria,
                only_active=only_active,
            )
        else:
            tenant_sessionmaker = get_tenant_sessionmaker(tenant)
            async with tenant_sessionmaker() as session:
                session.info["tenant_key"] = tenant
                solicitudes = await _fetch_client_requests_in_session(
                    session,
                    user_email=current_user.email,
                    diagnostico_categoria=diagnostico_categoria,
                    only_active=only_active,
                )
        for solicitud in solicitudes:
            _apply_cost_estimate(solicitud)
            setattr(solicitud, "tenant_key", tenant)
            responses.append(SolicitudResponse.model_validate(solicitud))
    responses.sort(key=lambda item: item.fecha_solicitud, reverse=True)
    return responses


def _serialize_services(services: str) -> list[str]:
    return [item for item in services.split("|") if item]


def _tipo_incidente_label(solicitud: Solicitud) -> str:
    """Nombre legible del tipo de incidente (Eléctrico, Gomería, Diagnóstico…).

    Se usa para que el taller vea de un vistazo —en la notificación y el push—
    qué tipo de problema entra. Devuelve "Incidente" si la solicitud no tiene
    tipo asociado. Requiere que `solicitud.tipo_incidente` esté precargado
    (selectinload); todas las rutas que lo usan cargan la solicitud vía
    _load_request_with_relations, por lo que no dispara lazy-load en async.
    """
    tipo = getattr(solicitud, "tipo_incidente", None)
    nombre = getattr(tipo, "nombre", None) if tipo is not None else None
    return (nombre or "").strip() or "Incidente"


async def _ensure_on_demand_service(
    db: AsyncSession,
    solicitud: Solicitud,
    *,
    latitud_cliente: float,
    longitud_cliente: float,
    radio_busqueda_km: float = 25.0,
) -> ServicioTallerDemanda:
    # SQLAlchemy async no permite lazy-load fuera de un await — si el
    # caller no precargó `servicio_demanda` con selectinload, acceder a la
    # relación dispara MissingGreenlet. Lo consultamos explícitamente.
    if "servicio_demanda" in solicitud.__dict__:
        servicio = solicitud.servicio_demanda
    else:
        servicio = await db.scalar(
            select(ServicioTallerDemanda).where(
                ServicioTallerDemanda.solicitud_id == solicitud.id,
            )
        )
    if servicio is None:
        servicio = ServicioTallerDemanda(
            solicitud_id=solicitud.id,
            estado="BUSCANDO",
            latitud_cliente=latitud_cliente,
            longitud_cliente=longitud_cliente,
            latitud_servicio=solicitud.latitud_incidente,
            longitud_servicio=solicitud.longitud_incidente,
            direccion_servicio=solicitud.ubicacion_texto,
            radio_busqueda_km=radio_busqueda_km,
        )
        db.add(servicio)
        await db.flush()
        solicitud.servicio_demanda = servicio
        return servicio

    servicio.latitud_cliente = latitud_cliente
    servicio.longitud_cliente = longitud_cliente
    servicio.latitud_servicio = solicitud.latitud_incidente
    servicio.longitud_servicio = solicitud.longitud_incidente
    servicio.direccion_servicio = solicitud.ubicacion_texto
    servicio.radio_busqueda_km = radio_busqueda_km
    return servicio


def _reset_on_demand_service(servicio: ServicioTallerDemanda | None) -> None:
    if servicio is None:
        return
    servicio.estado = "BUSCANDO"
    servicio.taller_id = None
    servicio.tecnico_id = None
    servicio.cobertura_tecnico_km = None
    servicio.distancia_asignacion_km = None
    servicio.eta_estimado_min = None
    servicio.score_matching = None
    servicio.match_especialidad = False
    servicio.detalle_matching = None
    servicio.confirmacion_ubicacion_ok = None
    servicio.latitud_confirmacion_final = None
    servicio.longitud_confirmacion_final = None
    servicio.distancia_confirmacion_m = None
    servicio.confirmacion_ubicacion_en = None


def _parse_ai_tags(tags: str | None) -> list[str]:
    return [item for item in (tags or "").split("|") if item]


def _normalize_text_for_diagnostic(*parts: str | None) -> str:
    return " ".join((part or "").strip().lower() for part in parts if part and part.strip())


def _is_empty_tank_request(
    tipo_incidente: str | None,
    descripcion: str | None,
    tags: list[str] | None = None,
) -> bool:
    normalized_text = _normalize_text_for_diagnostic(tipo_incidente, descripcion, " ".join(tags or []))
    return any(
        needle in normalized_text
        for needle in (
            "sin combustible",
            "sin gasolina",
            "sin diesel",
            "sin diésel",
            "tanque vacio",
            "tanque vacío",
            "quedo sin combustible",
            "quedó sin combustible",
        )
    )


def _specialize_diagnostic_tags(
    *,
    tipo_incidente: str | None,
    descripcion: str | None,
    tags: list[str] | None,
) -> list[str]:
    empty_tank = _is_empty_tank_request(tipo_incidente, descripcion, tags)
    normalized: list[str] = []
    for tag in tags or []:
        cleaned = (tag or "").strip().lower()
        if not cleaned:
            continue
        if cleaned in {"combustible", "tanque", "tanque_vacio", "tanque vacío", "tanque vacio"}:
            normalized.append("tanque_vacio" if empty_tank else "combustible")
            continue
        normalized.append(cleaned)
    if empty_tank:
        normalized.append("tanque_vacio")
    return sorted(set(normalized))


def _build_technical_diagnostic_summary(
    *,
    tipo_incidente: str | None,
    descripcion: str | None,
    base_summary: str | None,
    requires_manual_review: bool,
    tags: list[str] | None,
) -> str:
    technical_tags = _specialize_diagnostic_tags(
        tipo_incidente=tipo_incidente,
        descripcion=descripcion,
        tags=tags,
    )
    prefix = "Diagnóstico técnico generado automáticamente."
    if "tanque_vacio" in technical_tags:
        prefix = "Diagnóstico técnico: tanque vacío."
    if requires_manual_review:
        return f"{prefix} Diagnóstico pendiente de validación técnica manual."
    if base_summary:
        return f"{prefix} {base_summary}".strip()
    return prefix


def _merge_ai_tags(existing_tags: str | None, new_tags: list[str]) -> str | None:
    merged = sorted(set(_parse_ai_tags(existing_tags) + [tag for tag in new_tags if tag]))
    return "|".join(merged) if merged else None


def _has_cross_tenant_request_visibility(roles: set[str]) -> bool:
    return bool(roles.intersection({"ADMINISTRADOR", "OPERADOR"}))


def _region_hint_from_request(solicitud: Solicitud) -> str | None:
    candidates = [
        (solicitud.cliente.direccion if solicitud.cliente else None),
        solicitud.descripcion,
    ]
    for raw in candidates:
        if raw and raw.strip():
            return raw.strip()
    return None


def _parse_visual_signal_metadata(raw_value: str | None) -> dict | None:
    if not raw_value:
        return None
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _collect_visual_signals(solicitud: Solicitud) -> list[dict]:
    visual_signals: list[dict] = []
    for evidence in solicitud.evidencias:
        if evidence.tipo != "IMAGE":
            continue
        parsed = _parse_visual_signal_metadata(evidence.contenido_texto)
        if parsed and parsed.get("status") == "OK":
            visual_signals.append(parsed)
    return visual_signals


def _resolve_evidence_storage_path(evidence: EvidenciaSolicitud) -> Path | None:
    backend_root = Path(__file__).resolve().parents[2]
    storage_dir = EVIDENCE_STORAGE_DIR
    storage_dir.mkdir(parents=True, exist_ok=True)

    if evidence.archivo_url:
        candidate = (backend_root / evidence.archivo_url).resolve()
        if str(candidate).lower().startswith(str(backend_root.resolve()).lower()) and candidate.is_file():
            return candidate

    suffix = Path(evidence.nombre_archivo or "").suffix
    candidates: list[Path] = []
    if evidence.solicitud_id:
        pattern = f"solicitud_{evidence.solicitud_id}_*{suffix}" if suffix else f"solicitud_{evidence.solicitud_id}_*"
        candidates.extend([item for item in storage_dir.glob(pattern) if item.is_file()])
    if candidates:
        return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0]
    return None


def _apply_cost_estimate(solicitud: Solicitud) -> None:
    visual_signals = _collect_visual_signals(solicitud)
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
        visual_signals=visual_signals,
    )
    solicitud.costo_estimado = estimation.amount
    solicitud.costo_estimado_min = estimation.min_amount
    solicitud.costo_estimado_max = estimation.max_amount
    solicitud.costo_estimacion_confianza = estimation.confidence
    solicitud.costo_estimacion_nota = estimation.note
    setattr(solicitud, "visual_tags", estimation.visual_tags)
    setattr(solicitud, "visual_summary", estimation.visual_summary)
    setattr(solicitud, "visual_factor", estimation.visual_factor)
    setattr(solicitud, "visual_confidence", estimation.visual_confidence)
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
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    email = payload.get("sub")
    if not isinstance(email, str) or not email.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    token_tenant = payload.get("tenant")
    request_tenant = getattr(request.state, "tenant_key", None)
    if isinstance(token_tenant, str) and isinstance(request_tenant, str) and token_tenant.strip() and request_tenant.strip():
        if token_tenant.strip() != request_tenant.strip():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido para este tenant")
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


async def _dispatch_push_notifications(
    db: AsyncSession,
    user_ids: list[int],
    titulo: str,
    mensaje: str,
    tipo: str,
    deep_link: str | None = None,
    diagnostico_categoria: str | None = None,
) -> None:
    if not user_ids:
        return
    tenant_key = db.info.get("tenant_key", settings.default_tenant or "default")
    unique_user_ids = list(set(user_ids))
    allowed_user_ids = await _filter_notification_recipient_ids(db, unique_user_ids, tipo)
    # #region debug-point C:dispatch-evaluated
    _debug_report(
        "C",
        "backend/app/routers/gestion_solicitudes/solicitudes.py:_dispatch_push_notifications",
        "notification recipients evaluated",
        {
            "tenant": tenant_key,
            "tipo": tipo,
            "requested_user_ids": unique_user_ids,
            "allowed_user_ids": allowed_user_ids,
            "deep_link": deep_link,
        },
    )
    # #endregion
    if not allowed_user_ids:
        return

    result = await db.execute(select(UserDeviceToken).where(UserDeviceToken.user_id.in_(allowed_user_ids)))
    device_tokens = result.scalars().all()
    # #region debug-point B:mobile-device-tokens
    _debug_report(
        "B",
        "backend/app/routers/gestion_solicitudes/solicitudes.py:_dispatch_push_notifications",
        "resolved mobile device tokens for allowed recipients",
        {
            "tenant": tenant_key,
            "tipo": tipo,
            "allowed_user_ids": allowed_user_ids,
            "device_token_count": len(device_tokens),
            "device_token_user_ids": sorted({device_token.user_id for device_token in device_tokens}),
            "platforms": sorted({(device_token.plataforma or "").strip() or "unknown" for device_token in device_tokens}),
        },
    )
    # #endregion
    for device_token in device_tokens:
        data = {"type": tipo, "user_id": str(device_token.user_id)}
        if deep_link:
            data["url"] = deep_link
        if diagnostico_categoria:
            data["diagnostico_categoria"] = diagnostico_categoria
        # #region debug-point A:mobile-push-send-attempt
        _debug_report(
            "A",
            "backend/app/routers/gestion_solicitudes/solicitudes.py:_dispatch_push_notifications",
            "sending mobile push notification",
            {
                "tenant": tenant_key,
                "tipo": tipo,
                "target_user_id": device_token.user_id,
                "platform": device_token.plataforma,
                "deep_link": deep_link,
                "payload_keys": sorted(data.keys()),
                "token_suffix": device_token.token[-12:] if device_token.token else "",
            },
        )
        # #endregion
        enviar_notificacion_push(device_token.token, titulo, mensaje, data)

    if settings.vapid_private_key and settings.vapid_public_key:
        subscriptions_result = await db.execute(select(WebPushSubscription).where(WebPushSubscription.user_id.in_(allowed_user_ids)))
        subscriptions = subscriptions_result.scalars().all()
        subscriptions_by_endpoint: dict[str, list[WebPushSubscription]] = {}
        for subscription in subscriptions:
            subscriptions_by_endpoint.setdefault(subscription.endpoint, []).append(subscription)
        # #region debug-point C:webpush-subscriptions
        _debug_report(
            "C",
            "backend/app/routers/gestion_solicitudes/solicitudes.py:_dispatch_push_notifications",
            "web push subscriptions resolved for recipients",
            {
                "tenant": tenant_key,
                "tipo": tipo,
                "allowed_user_ids": allowed_user_ids,
                "subscription_count": len(subscriptions_by_endpoint),
                "subscription_user_ids": sorted({subscription.user_id for subscription in subscriptions}),
                "subscription_endpoints": [endpoint[-24:] for endpoint in subscriptions_by_endpoint],
            },
        )
        # #endregion
        to_delete: list[WebPushSubscription] = []
        for endpoint, endpoint_subscriptions in subscriptions_by_endpoint.items():
            subscription = endpoint_subscriptions[0]
            subscription_info = {"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}}
            data = {"type": tipo, "user_id": str(subscription.user_id)}
            if deep_link:
                data["url"] = deep_link
            if diagnostico_categoria:
                data["diagnostico_categoria"] = diagnostico_categoria
            ok = enviar_web_push(
                subscription_info=subscription_info,
                titulo=titulo,
                mensaje=mensaje,
                data=data,
                vapid_private_key=settings.vapid_private_key,
                vapid_subject=settings.vapid_subject,
            )
            if not ok:
                to_delete.extend(endpoint_subscriptions)
        for subscription in to_delete:
            await db.delete(subscription)
        if to_delete:
            await db.commit()


async def _filter_notification_recipient_ids(
    db: AsyncSession,
    user_ids: list[int],
    tipo: str,
) -> list[int]:
    unique_user_ids = list(set(user_ids))
    if not unique_user_ids:
        return []
    prefs_result = await db.execute(
        select(UserNotificationPreferences).where(UserNotificationPreferences.user_id.in_(unique_user_ids))
    )
    prefs_by_user = {pref.user_id: pref for pref in prefs_result.scalars().all()}
    allowed_user_ids: list[int] = []
    for user_id in unique_user_ids:
        prefs = prefs_by_user.get(user_id)
        if prefs and prefs.disabled_all:
            continue
        if prefs and (prefs.disabled_types or {}).get(tipo):
            continue
        allowed_user_ids.append(user_id)
    return allowed_user_ids


async def _notify_users(
    db: AsyncSession,
    user_ids: list[int],
    titulo: str,
    mensaje: str,
    tipo: str,
    deep_link: str | None = None,
    diagnostico_categoria: str | None = None,
) -> None:
    tenant_key = db.info.get("tenant_key", settings.default_tenant or "default")
    allowed_user_ids = await _filter_notification_recipient_ids(db, user_ids, tipo)
    # #region debug-point C:notify-called
    _debug_report(
        "C",
        "backend/app/routers/gestion_solicitudes/solicitudes.py:_notify_users",
        "notify users called",
        {
            "tenant": tenant_key,
            "tipo": tipo,
            "titulo": titulo,
            "user_ids": sorted(set(user_ids)),
            "deep_link": deep_link,
        },
    )
    # #endregion
    for user_id in set(user_ids):
        db.add(
            Notificacion(
                usuario_id=user_id,
                titulo=titulo,
                mensaje=mensaje,
                tipo=tipo,
                diagnostico_categoria=diagnostico_categoria,
            )
        )
    try:
        await _realtime_hub.broadcast_notification_event(
            tenant_key,
            user_ids=allowed_user_ids,
            titulo=titulo,
            mensaje=mensaje,
            tipo=tipo,
            deep_link=deep_link,
            diagnostico_categoria=diagnostico_categoria,
        )
    except Exception:
        pass
    await _dispatch_push_notifications(db, user_ids, titulo, mensaje, tipo, deep_link, diagnostico_categoria)


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
        match = calcular_match_tecnico(solicitud, tecnico, max_radio_km=radio_km)
        if match is None:
            continue
        encontrados.append(
            TecnicoCandidatoResponse(
                id=tecnico.id,
                nombre=tecnico.nombre,
                telefono=tecnico.telefono,
                especialidad=tecnico.especialidad,
                disponibilidad=tecnico.disponibilidad,
                en_turno=tecnico.en_turno,
                radio_cobertura_km=tecnico.radio_cobertura_km,
                match_especialidad=match.match_especialidad,
                score=match.score,
                detalle_match=match.detalle,
                distancia_km=match.distancia_km,
                eta_min=match.eta_min,
            )
        )
    return sorted(encontrados, key=lambda item: ((item.score or 0) * -1, item.distancia_km or 0))


# Caché en proceso de rutas viales taller→incidente para el seguimiento. La
# geometría es estática para un par de coordenadas, así que evitamos llamar a
# Mapbox Directions en cada poll (la UI consulta el seguimiento cada pocos
# segundos). Clave: coordenadas redondeadas a 5 decimales (~1 m).
_TRACKING_ROUTE_CACHE: dict[tuple[float, float, float, float], MapboxRoute] = {}
_TRACKING_ROUTE_CACHE_MAX = 256


async def _tracking_route_taller_incidente(
    taller_lat: float, taller_lon: float, inc_lat: float, inc_lon: float
) -> MapboxRoute | None:
    """Ruta vial taller→incidente para dibujar el camino del seguimiento.
    Devuelve None si Mapbox Directions falla (la UI cae a Haversine)."""
    key = (round(taller_lat, 5), round(taller_lon, 5), round(inc_lat, 5), round(inc_lon, 5))
    cached = _TRACKING_ROUTE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        ruta = await route_driving(
            origen_lat=taller_lat, origen_lon=taller_lon,
            destino_lat=inc_lat, destino_lon=inc_lon,
        )
    except Exception:
        return None
    if len(_TRACKING_ROUTE_CACHE) >= _TRACKING_ROUTE_CACHE_MAX:
        _TRACKING_ROUTE_CACHE.clear()
    _TRACKING_ROUTE_CACHE[key] = ruta
    return ruta


async def _build_tracking_response(solicitud: Solicitud) -> SolicitudSeguimientoResponse:
    estado = solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO"
    tecnico = solicitud.tecnico
    propuesta_expirada = _is_client_approval_expired(solicitud)
    servicio = solicitud.servicio_demanda
    base_payload = {
        "solicitud_id": solicitud.id,
        "estado": estado,
        "route_color": tracking_route_color(solicitud.id),
        "servicio_id": servicio.id if servicio else None,
        "servicio_estado": servicio.estado if servicio else None,
        "taller_nombre": solicitud.taller.nombre if solicitud.taller else None,
        "taller_id": solicitud.taller_id,
        "latitud_taller": solicitud.taller.latitud if solicitud.taller else None,
        "longitud_taller": solicitud.taller.longitud if solicitud.taller else None,
        "cliente_aprobada": solicitud.cliente_aprobada,
        "propuesta_expira_en": solicitud.propuesta_expira_en,
        "propuesta_expirada": propuesta_expirada,
        "latitud_cliente": servicio.latitud_cliente if servicio else None,
        "longitud_cliente": servicio.longitud_cliente if servicio else None,
        "latitud_servicio": servicio.latitud_servicio if servicio else solicitud.latitud_incidente,
        "longitud_servicio": servicio.longitud_servicio if servicio else solicitud.longitud_incidente,
        "direccion_servicio": servicio.direccion_servicio if servicio else solicitud.ubicacion_texto,
        "match_especialidad": servicio.match_especialidad if servicio else False,
        "confirmacion_ubicacion_ok": servicio.confirmacion_ubicacion_ok if servicio else None,
        "distancia_confirmacion_m": servicio.distancia_confirmacion_m if servicio else None,
        "confirmacion_ubicacion_en": servicio.confirmacion_ubicacion_en if servicio else None,
    }
    if solicitud.taller_id and solicitud.cliente_aprobada is False and not tecnico:
        return SolicitudSeguimientoResponse(
            **base_payload,
            tracking_activo=False,
            mensaje=(
                "La propuesta está pendiente de aprobación del cliente."
                if not propuesta_expirada
                else "La propuesta expiró y requiere una nueva asignación."
            ),
        )
    if not tecnico:
        # Fallback sin técnico: si el taller tiene coordenadas usamos el
        # propio taller como origen del seguimiento (taller → incidente), así
        # un taller sin técnicos igual muestra la ruta y un ETA estimado.
        taller = solicitud.taller
        if taller and taller.latitud is not None and taller.longitud is not None:
            llego = estado in {"EN_ATENCION", "COMPLETADA"}
            # Ruta vial real taller→incidente (cacheada) para que el camino siga
            # las calles, no una recta. Da geometría + distancia/ETA calibrados.
            ruta = await _tracking_route_taller_incidente(
                taller.latitud, taller.longitud,
                solicitud.latitud_incidente, solicitud.longitud_incidente,
            )
            # Sólo propagamos la geometría si es una polyline vial real (≥3 pts);
            # el cliente rechaza el fallback de 2 puntos por considerarlo recta.
            ruta_seguimiento = None
            if ruta is not None and isinstance(ruta.geometry, dict):
                coords = ruta.geometry.get("coordinates")
                if isinstance(coords, list) and len(coords) >= 3:
                    ruta_seguimiento = ruta.geometry

            if llego:
                # Ya está en el lugar: el móvil del equipo se ubica sobre el incidente.
                origen_lat = solicitud.latitud_incidente
                origen_lon = solicitud.longitud_incidente
                distancia = 0.0
                # Ya llegó: el ETA restante es 0. No llamamos al estimador
                # porque rechaza distancia 0 (exige positiva y finita) y
                # lanzaría TravelTimeRangeError, reventando el seguimiento
                # (500) y dejando el detalle móvil/web en blanco.
                eta = 0
                eta_lower, eta_upper = 0, 0
            else:
                origen_lat = taller.latitud
                origen_lon = taller.longitud
                if ruta is not None:
                    distancia = ruta.distance_km
                    eta = int(round(ruta.duration_min))
                    eta_lower, eta_upper = ruta.duration_range_min
                else:
                    distancia = calcular_distancia_km(
                        solicitud.latitud_incidente, solicitud.longitud_incidente,
                        taller.latitud, taller.longitud,
                    )
                    eta = estimate_eta_minutes(distancia)
                    eta_lower, eta_upper = estimate_eta_range_minutes(distancia)
            return SolicitudSeguimientoResponse(
                **base_payload,
                latitud_actual=origen_lat,
                longitud_actual=origen_lon,
                distancia_km=round(distancia, 2),
                eta_min=eta,
                eta_min_lower=eta_lower,
                eta_min_upper=eta_upper,
                ruta_seguimiento=ruta_seguimiento,
                tracking_activo=True,
                mensaje=(
                    "El equipo del taller llegó al lugar del incidente."
                    if llego
                    else "Ruta estimada desde el taller hasta el incidente."
                ),
            )
        return SolicitudSeguimientoResponse(
            **base_payload,
            tracking_activo=False,
            mensaje="La solicitud aún no tiene un técnico confirmado.",
        )
    if tecnico.latitud_actual is None or tecnico.longitud_actual is None:
        return SolicitudSeguimientoResponse(
            **base_payload,
            tecnico_id=tecnico.id,
            tecnico_nombre=tecnico.nombre,
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
    eta_lower, eta_upper = estimate_eta_range_minutes(distancia)
    return SolicitudSeguimientoResponse(
        **base_payload,
        tecnico_id=tecnico.id,
        tecnico_nombre=tecnico.nombre,
        latitud_actual=tecnico.latitud_actual,
        longitud_actual=tecnico.longitud_actual,
        distancia_km=round(distancia, 2),
        eta_min=estimate_eta_minutes(distancia),
        eta_min_lower=eta_lower,
        eta_min_upper=eta_upper,
        ubicacion_actualizada_en=tecnico.ubicacion_actualizada_en,
        ubicacion_desactualizada=is_stale,
        tracking_activo=not is_stale,
        sin_senal=is_stale,
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
    if roles.intersection({"ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR"}):
        return
    if "CLIENTE" in roles and current_cliente_id == solicitud.cliente_id:
        return
    if "TECNICO" in roles and current_tecnico_id == solicitud.tecnico_id:
        return
    if "TALLER" in roles and current_taller_id == solicitud.taller_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes acceder a esta solicitud")


async def _asignar_taller_elegido_por_cliente(
    *,
    db: AsyncSession,
    solicitud: Solicitud,
    taller_id: int,
    latitud_cliente: float,
    longitud_cliente: float,
    presupuesto_aceptado: float | None,
    estado_registrada_nombre: str,
    usuario_id: int,
) -> None:
    """Procesa la asignación automática cuando el cliente preseleccionó un taller desde el mapa.

    - Avanza el estado a ASIGNADA (sin intervención de operador).
    - Calcula la ruta cliente → taller si Mapbox está disponible.
    - Notifica al taller que el cliente está en camino.
    """
    taller = await db.get(Taller, taller_id)
    if not taller or not taller.disponible:
        # Taller no encontrado o no disponible: mantenemos REGISTRADA sin abortar la creación.
        return

    estado_asignada = await _get_estado_por_nombre(db, "ASIGNADA")
    solicitud.estado_id = estado_asignada.id
    solicitud.fecha_asignacion = datetime.now(timezone.utc)
    if presupuesto_aceptado is not None:
        solicitud.presupuesto_aceptado = presupuesto_aceptado

    # Calcular ruta (falla silenciosamente para no bloquear la creación)
    ruta = None
    try:
        ruta = await route_driving(
            origen_lat=latitud_cliente,
            origen_lon=longitud_cliente,
            destino_lat=taller.latitud,
            destino_lon=taller.longitud,
        )
    except Exception:
        pass

    if ruta:
        solicitud.ruta_osrm = ruta.geometry
        solicitud.ruta_distancia_km = ruta.distance_km
        solicitud.ruta_eta_min = int(round(ruta.duration_min))

    servicio = solicitud.servicio_demanda
    if servicio:
        servicio.taller_id = taller.id
        servicio.estado = "CLIENTE_EN_CAMINO"
        servicio.detalle_matching = "Taller elegido por el cliente desde el mapa de asistencia."
        if ruta:
            servicio.distancia_asignacion_km = ruta.distance_km
            servicio.eta_estimado_min = int(round(ruta.duration_min))

    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_registrada_nombre,
            estado_nuevo=estado_asignada.nombre,
            observacion=(
                f"Taller '{taller.nombre}' seleccionado por el cliente desde el mapa. "
                "Asignación automática sin intervención de operador."
            ),
            usuario_id=usuario_id,
        )
    )

    # Notificar al taller: "El cliente está en camino"
    eta_label = f" ETA estimado: {ruta.duration_min:.0f} min." if ruta else ""
    if taller.user_id:
        db.add(
            Notificacion(
                usuario_id=taller.user_id,
                titulo="Cliente en camino",
                mensaje=(
                    f"Un cliente eligió tu taller para la solicitud #{solicitud.id}.{eta_label} "
                    "Prepárate para recibirlo."
                ),
                tipo="CLIENTE_EN_CAMINO",
            )
        )
        await _notify_users(
            db,
            [taller.user_id],
            "Cliente en camino",
            f"Solicitud #{solicitud.id}: el cliente está en camino a tu taller.{eta_label}",
            "CLIENTE_EN_CAMINO",
            deep_link=f"/solicitudes/{solicitud.id}",
        )


async def _ensure_client_profile_in_tenant(
    *,
    tenant_db: AsyncSession,
    current_user: User,
    source_cliente: Cliente,
    source_vehiculo: Vehiculo,
) -> tuple[User, Cliente, Vehiculo]:
    role = await tenant_db.scalar(select(Role).where(Role.name == "CLIENTE"))
    if role is None:
        role = Role(name="CLIENTE")
        tenant_db.add(role)
        await tenant_db.flush()

    user = await tenant_db.scalar(select(User).where(User.email == current_user.email))
    if user is None:
        user = User(email=current_user.email, password_hash=hash_password("tenant-sync"), is_active=True)
        user.roles.append(role)
        tenant_db.add(user)
        await tenant_db.flush()
    else:
        if role not in user.roles:
            user.roles.append(role)

    cliente = await tenant_db.scalar(select(Cliente).where(Cliente.user_id == user.id))
    if cliente is None:
        cliente = Cliente(
            user_id=user.id,
            nombre=source_cliente.nombre,
            telefono=source_cliente.telefono,
            direccion=source_cliente.direccion,
        )
        tenant_db.add(cliente)
        await tenant_db.flush()

    vehiculo = await tenant_db.scalar(select(Vehiculo).where(Vehiculo.placa == source_vehiculo.placa))
    if vehiculo is None:
        vehiculo = Vehiculo(
            cliente_id=cliente.id,
            marca=source_vehiculo.marca,
            modelo=source_vehiculo.modelo,
            anio=source_vehiculo.anio,
            placa=source_vehiculo.placa,
            color=source_vehiculo.color,
            tipo_combustible=source_vehiculo.tipo_combustible,
        )
        tenant_db.add(vehiculo)
        await tenant_db.flush()
    elif vehiculo.cliente_id != cliente.id:
        vehiculo.cliente_id = cliente.id

    await _sync_notification_channels_to_tenant_user(
        tenant_db=tenant_db,
        target_user=user,
        user_email=current_user.email,
    )
    return user, cliente, vehiculo


async def _sync_notification_channels_to_tenant_user(
    *,
    tenant_db: AsyncSession,
    target_user: User,
    user_email: str,
) -> None:
    current_tenant = tenant_db.info.get("tenant_key", settings.default_tenant or "default")
    tokens_by_value: dict[str, str] = {}
    subscriptions_by_endpoint: dict[str, tuple[str, str, str | None, str | None]] = {}

    for tenant in settings.tenant_databases:
        if tenant == current_tenant:
            session = tenant_db
            owns_session = False
        else:
            session = get_tenant_sessionmaker(tenant)()
            session.info["tenant_key"] = tenant
            owns_session = True
        try:
            source_user = await session.scalar(select(User).where(User.email == user_email))
            if source_user is None:
                continue

            token_result = await session.execute(
                select(UserDeviceToken).where(UserDeviceToken.user_id == source_user.id)
            )
            for token in token_result.scalars().all():
                tokens_by_value[token.token] = token.plataforma

            subscription_result = await session.execute(
                select(WebPushSubscription).where(WebPushSubscription.user_id == source_user.id)
            )
            for subscription in subscription_result.scalars().all():
                subscriptions_by_endpoint[subscription.endpoint] = (
                    subscription.p256dh,
                    subscription.auth,
                    subscription.expiration_time,
                    subscription.user_agent,
                )
        finally:
            if owns_session:
                await session.close()

    if tokens_by_value:
        existing_tokens_result = await tenant_db.execute(
            select(UserDeviceToken).where(UserDeviceToken.user_id == target_user.id)
        )
        existing_tokens = {row.token: row for row in existing_tokens_result.scalars().all()}
        for token, plataforma in tokens_by_value.items():
            existing = existing_tokens.get(token)
            if existing:
                existing.plataforma = plataforma
                continue
            tenant_db.add(
                UserDeviceToken(
                    user_id=target_user.id,
                    token=token,
                    plataforma=plataforma,
                )
            )

    if subscriptions_by_endpoint:
        existing_subscriptions_result = await tenant_db.execute(
            select(WebPushSubscription).where(WebPushSubscription.user_id == target_user.id)
        )
        existing_subscriptions = {
            row.endpoint: row for row in existing_subscriptions_result.scalars().all()
        }
        for endpoint, (p256dh, auth, expiration_time, user_agent) in subscriptions_by_endpoint.items():
            existing = existing_subscriptions.get(endpoint)
            if existing:
                existing.p256dh = p256dh
                existing.auth = auth
                existing.expiration_time = expiration_time
                existing.user_agent = user_agent
                continue
            tenant_db.add(
                WebPushSubscription(
                    user_id=target_user.id,
                    endpoint=endpoint,
                    p256dh=p256dh,
                    auth=auth,
                    expiration_time=expiration_time,
                    user_agent=user_agent,
                )
            )


async def _create_request_in_session(
    *,
    db: AsyncSession,
    payload: SolicitudCreate,
    cliente: Cliente,
    vehiculo: Vehiculo,
    tipo_incidente: TipoIncidente,
    usuario_id: int,
) -> Solicitud:
    estado_registrada = await _get_estado_por_nombre(db, "REGISTRADA")
    diagnostico_categoria = resolve_categoria_diagnostico(
        raw=payload.categoria_dano,
        tipo_incidente=tipo_incidente.nombre,
        descripcion=payload.descripcion,
    ).value
    nivel_riesgo = infer_risk_level(
        tipo_incidente=tipo_incidente.nombre,
        descripcion=payload.descripcion,
        es_carretera=payload.es_carretera,
        condicion_vehiculo=payload.condicion_vehiculo,
        hint=payload.nivel_riesgo,
    )
    prioridad = calcular_prioridad(
        tipo_incidente=tipo_incidente.nombre,
        es_carretera=payload.es_carretera,
        condicion_vehiculo=payload.condicion_vehiculo,
        nivel_riesgo=nivel_riesgo,
        categoria_dano=diagnostico_categoria,
    )
    triage = analyze_incident(
        tipo_incidente=tipo_incidente.nombre,
        descripcion=payload.descripcion,
        es_carretera=payload.es_carretera,
        condicion_vehiculo=payload.condicion_vehiculo,
        nivel_riesgo=nivel_riesgo,
    )
    technical_tags = _specialize_diagnostic_tags(
        tipo_incidente=tipo_incidente.nombre,
        descripcion=payload.descripcion,
        tags=triage.detected_tags,
    )
    technical_summary = _build_technical_diagnostic_summary(
        tipo_incidente=tipo_incidente.nombre,
        descripcion=payload.descripcion,
        base_summary=triage.summary,
        requires_manual_review=triage.requires_manual_review,
        tags=technical_tags,
    )
    solicitud = Solicitud(
        cliente_id=cliente.id,
        vehiculo_id=vehiculo.id,
        taller_id=payload.taller_id,
        tipo_incidente_id=tipo_incidente.id,
        estado_id=estado_registrada.id,
        latitud_incidente=payload.latitud_incidente,
        longitud_incidente=payload.longitud_incidente,
        descripcion=payload.descripcion,
        foto_url=payload.foto_url,
        es_carretera=payload.es_carretera,
        condicion_vehiculo=payload.condicion_vehiculo,
        nivel_riesgo=nivel_riesgo,
        fecha_incidente=payload.fecha_incidente,
        danos_descripcion=payload.danos_descripcion,
        ubicacion_texto=payload.ubicacion_texto,
        categoria_dano=diagnostico_categoria,
        clasificacion_confianza=triage.confidence,
        requiere_revision_manual=triage.requires_manual_review,
        motivo_prioridad=triage.reason,
        resumen_ia=technical_summary,
        etiquetas_ia="|".join(technical_tags),
        proveedor_ia=triage.provider,
        prioridad=prioridad,
    )
    solicitud.tipo_incidente = tipo_incidente
    solicitud.vehiculo = vehiculo
    solicitud.cliente = cliente
    _apply_cost_estimate(solicitud)
    db.add(solicitud)
    await db.flush()
    await _ensure_on_demand_service(
        db,
        solicitud,
        latitud_cliente=payload.latitud_cliente,
        longitud_cliente=payload.longitud_cliente,
    )
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior="NUEVA",
            estado_nuevo=estado_registrada.nombre,
            observacion=f"Solicitud creada por el cliente. Diagnóstico: {diagnostico_categoria}",
            usuario_id=usuario_id,
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
                f"Costo estimado aproximado: {format_bs(solicitud.costo_estimado)}. "
                f"Diagnóstico: {diagnostico_categoria}"
            ),
            usuario_id=usuario_id,
        )
    )
    await _notify_users(
        db,
        [usuario_id],
        "Solicitud registrada",
        (
            f"Tu solicitud #{solicitud.id} fue registrada con prioridad {prioridad.value}. "
            f"Diagnóstico: {diagnostico_categoria}."
        ),
        "SOLICITUD_REGISTRADA",
        deep_link=f"/solicitudes/{solicitud.id}",
        diagnostico_categoria=diagnostico_categoria,
    )
    if triage.requires_manual_review:
        operador_ids = await _get_operador_user_ids(db)
        await _notify_users(
            db,
            operador_ids,
            "Revisión manual requerida",
            (
                f"La solicitud #{solicitud.id} necesita validación operativa por confianza {triage.confidence:.2f}. "
                f"Diagnóstico: {diagnostico_categoria}."
            ),
            "REVISION_MANUAL",
            deep_link=f"/solicitudes/{solicitud.id}",
            diagnostico_categoria=diagnostico_categoria,
        )
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_registrada.nombre,
                estado_nuevo=estado_registrada.nombre,
                observacion=f"Solicitud derivada a revisión manual por baja confianza. Diagnóstico: {diagnostico_categoria}",
                usuario_id=usuario_id,
            )
        )
    if prioridad.value == "CRITICA":
        operador_ids = await _get_operador_user_ids(db)
        await _notify_users(
            db,
            operador_ids,
            "Incidente crítico escalado",
            f"La solicitud #{solicitud.id} requiere atención inmediata. Diagnóstico: {diagnostico_categoria}.",
            "ESCALAMIENTO_CRITICO",
            deep_link=f"/solicitudes/{solicitud.id}",
            diagnostico_categoria=diagnostico_categoria,
        )
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_registrada.nombre,
                estado_nuevo=estado_registrada.nombre,
                observacion=f"Escalada automática por prioridad crítica. Diagnóstico: {diagnostico_categoria}",
                usuario_id=usuario_id,
            )
        )
    if payload.taller_id is not None:
        await _asignar_taller_elegido_por_cliente(
            db=db,
            solicitud=solicitud,
            taller_id=payload.taller_id,
            latitud_cliente=payload.latitud_cliente,
            longitud_cliente=payload.longitud_cliente,
            presupuesto_aceptado=payload.presupuesto_aceptado,
            estado_registrada_nombre=estado_registrada.nombre,
            usuario_id=usuario_id,
        )
    await db.commit()
    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


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
    if payload.categoria_dano is not None and try_parse_categoria_dano(payload.categoria_dano) is None:
        logger.warning(
            "categoria_dano invalida en create_request user_id=%s cliente_id=%s tipo_incidente_id=%s raw=%r",
            current_user.id,
            payload.cliente_id,
            payload.tipo_incidente_id,
            payload.categoria_dano,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tipo de diagnóstico inválido. Usa una categoría autorizada.",
        )
    role_names = get_role_names(current_user)
    current_tenant = db.info.get("tenant_key", "default")
    target_tenant = await resolve_workshop_tenant_key(tipo_incidente_nombre=tipo_incidente.nombre)
    if not target_tenant:
        raise HTTPException(status_code=400, detail="No se pudo determinar el tenant de talleres para este incidente")

    if "CLIENTE" in role_names and target_tenant != current_tenant:
        from app.services.tenant_registry import tenant_registry

        if not tenant_registry.exists(target_tenant):
            raise HTTPException(status_code=400, detail=f"Tenant de talleres '{target_tenant}' no está provisionado")

        sessionmaker = get_tenant_sessionmaker(target_tenant)
        async with sessionmaker() as tenant_db:
            tipo_incidente_tenant = await tenant_db.scalar(
                select(TipoIncidente).where(TipoIncidente.nombre == tipo_incidente.nombre)
            )
            if tipo_incidente_tenant is None:
                tipo_incidente_tenant = TipoIncidente(
                    nombre=tipo_incidente.nombre,
                    descripcion=tipo_incidente.descripcion,
                )
                tenant_db.add(tipo_incidente_tenant)
                await tenant_db.flush()

            _, tenant_cliente, tenant_vehiculo = await _ensure_client_profile_in_tenant(
                tenant_db=tenant_db,
                current_user=current_user,
                source_cliente=cliente,
                source_vehiculo=vehiculo,
            )
            result = await _create_request_in_session(
                db=tenant_db,
                payload=payload,
                cliente=tenant_cliente,
                vehiculo=tenant_vehiculo,
                tipo_incidente=tipo_incidente_tenant,
                usuario_id=tenant_cliente.user_id,
            )
            setattr(result, "tenant_key", target_tenant)
            return result

    result = await _create_request_in_session(
        db=db,
        payload=payload,
        cliente=cliente,
        vehiculo=vehiculo,
        tipo_incidente=tipo_incidente,
        usuario_id=cliente.user_id,
    )
    setattr(result, "tenant_key", current_tenant)
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
    await _ensure_known_request_states(db)
    result = await db.execute(select(EstadoSolicitud).order_by(EstadoSolicitud.id))
    return list(result.scalars().all())


@router.get("/diagnosticos", response_model=list[str])
async def list_diagnostic_categories(
    _: User = Depends(get_current_user),
) -> list[str]:
    return [c.value for c in CategoriaDano]


@router.get("", response_model=list[SolicitudResponse])
async def list_requests(
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    diagnostico: str | None = Query(default=None, max_length=80),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[SolicitudResponse]:
    started = time.perf_counter()
    parsed_diagnostico = try_parse_categoria_dano(diagnostico) if diagnostico is not None else None
    if diagnostico is not None and parsed_diagnostico is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filtro de diagnóstico inválido. Usa una categoría autorizada.",
        )
    diagnostico_normalizado = parsed_diagnostico.value if parsed_diagnostico is not None else None
    query = (
        select(Solicitud)
        .options(
            selectinload(Solicitud.estado),
            selectinload(Solicitud.tipo_incidente),
            selectinload(Solicitud.servicio_demanda),
            selectinload(Solicitud.evidencias),
        )
        .order_by(desc(Solicitud.fecha_solicitud))
    )
    if diagnostico_normalizado is not None:
        query = query.where(Solicitud.categoria_dano == diagnostico_normalizado)
    roles = get_role_names(current_user)
    if "CLIENTE" in roles:
        if current_cliente_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Perfil de cliente no encontrado para este usuario",
            )
        return await _list_client_requests_across_tenants(
            db=db,
            current_user=current_user,
            diagnostico_categoria=diagnostico_normalizado,
            only_active=False,
        )
    elif "TECNICO" in roles and current_tecnico_id is not None:
        query = query.where(Solicitud.tecnico_id == current_tecnico_id)
    elif "TALLER" in roles and current_taller_id is not None:
        query = query.where(Solicitud.taller_id == current_taller_id)
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())
    for solicitud in items:
        _apply_cost_estimate(solicitud)
    logger.info(
        "list_requests tenant=%s roles=%s offset=%s limit=%s count=%s elapsed_ms=%s",
        db.info.get("tenant_key"),
        roles,
        offset,
        limit,
        len(items),
        round((time.perf_counter() - started) * 1000, 2),
    )
    return [SolicitudResponse.model_validate(item) for item in items]


@router.get("/activas", response_model=list[SolicitudResponse])
async def list_active_requests(
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    diagnostico: str | None = Query(default=None, max_length=80),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[SolicitudResponse]:
    started = time.perf_counter()
    parsed_diagnostico = try_parse_categoria_dano(diagnostico) if diagnostico is not None else None
    if diagnostico is not None and parsed_diagnostico is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filtro de diagnóstico inválido. Usa una categoría autorizada.",
        )
    diagnostico_normalizado = parsed_diagnostico.value if parsed_diagnostico is not None else None
    query = (
        select(Solicitud)
        .join(EstadoSolicitud)
        .options(
            selectinload(Solicitud.estado),
            selectinload(Solicitud.tipo_incidente),
            selectinload(Solicitud.servicio_demanda),
            selectinload(Solicitud.evidencias),
        )
        .where(EstadoSolicitud.nombre.not_in(["COMPLETADA", "CANCELADA"]))
        .order_by(desc(Solicitud.fecha_solicitud))
    )
    if diagnostico_normalizado is not None:
        query = query.where(Solicitud.categoria_dano == diagnostico_normalizado)
    roles = get_role_names(current_user)
    if "CLIENTE" in roles:
        if current_cliente_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Perfil de cliente no encontrado para este usuario",
            )
        return await _list_client_requests_across_tenants(
            db=db,
            current_user=current_user,
            diagnostico_categoria=diagnostico_normalizado,
            only_active=True,
        )
    elif "TECNICO" in roles and current_tecnico_id is not None:
        query = query.where(Solicitud.tecnico_id == current_tecnico_id)
    elif "TALLER" in roles and current_taller_id is not None:
        query = query.where(Solicitud.taller_id == current_taller_id)
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())
    for solicitud in items:
        _apply_cost_estimate(solicitud)
    logger.info(
        "list_active_requests tenant=%s roles=%s offset=%s limit=%s count=%s elapsed_ms=%s",
        db.info.get("tenant_key"),
        roles,
        offset,
        limit,
        len(items),
        round((time.perf_counter() - started) * 1000, 2),
    )
    return [SolicitudResponse.model_validate(item) for item in items]


@router.get("/historial/{cliente_id}", response_model=list[SolicitudResponse])
async def request_history(
    cliente_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    diagnostico: str | None = Query(default=None, max_length=80),
    db: AsyncSession = Depends(get_db),
) -> list[SolicitudResponse]:
    parsed_diagnostico = try_parse_categoria_dano(diagnostico) if diagnostico is not None else None
    if diagnostico is not None and parsed_diagnostico is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filtro de diagnóstico inválido. Usa una categoría autorizada.",
        )
    diagnostico_normalizado = parsed_diagnostico.value if parsed_diagnostico is not None else None
    if "CLIENTE" in get_role_names(current_user) and cliente_id != current_cliente_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes ver historial de otro cliente")
    if "CLIENTE" in get_role_names(current_user) and cliente_id == current_cliente_id:
        return await _list_client_requests_across_tenants(
            db=db,
            current_user=current_user,
            diagnostico_categoria=diagnostico_normalizado,
            only_active=False,
        )
    query = (
        select(Solicitud)
        .options(
            selectinload(Solicitud.estado),
            selectinload(Solicitud.tipo_incidente),
            selectinload(Solicitud.servicio_demanda),
            selectinload(Solicitud.evidencias),
        )
        .where(Solicitud.cliente_id == cliente_id)
        .order_by(desc(Solicitud.fecha_solicitud))
    )
    if diagnostico_normalizado is not None:
        query = query.where(Solicitud.categoria_dano == diagnostico_normalizado)
    result = await db.execute(query)
    items = list(result.scalars().all())
    for solicitud in items:
        _apply_cost_estimate(solicitud)
    return [SolicitudResponse.model_validate(item) for item in items]


@router.get("/{solicitud_id:int}", response_model=SolicitudResponse)
async def get_request(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> SolicitudResponse:
    # La solicitud puede vivir en el tenant del servicio (gomería→llaneros, …) y
    # no en el de login del cliente. Resolvemos cruzando tenants y serializamos
    # DENTRO del bloque para no tocar la sesión foránea una vez cerrada.
    async with _open_solicitud_session(
        db, solicitud_id, current_user,
        current_cliente_id, current_tecnico_id, current_taller_id,
    ) as (solicitud, _session, _usuario_id, cliente_id, tecnico_id, taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        validate_request_access(current_user, cliente_id, tecnico_id, taller_id, solicitud)
        _apply_cost_estimate(solicitud)
        return SolicitudResponse.model_validate(solicitud)


@router.get("/{solicitud_id:int}/detalle", response_model=SolicitudDetalleResponse)
async def get_request_detail(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> SolicitudDetalleResponse:
    # Resolución cross-tenant: la solicitud puede vivir en otro tenant. Todo el
    # armado (historial, evidencias, pagos, disputas) ocurre dentro del bloque
    # para no acceder a la sesión foránea una vez cerrada.
    async with _open_solicitud_session(
        db, solicitud_id, current_user,
        current_cliente_id, current_tecnico_id, current_taller_id,
    ) as (solicitud, _session, _usuario_id, cliente_id, tecnico_id, taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        validate_request_access(current_user, cliente_id, tecnico_id, taller_id, solicitud)
        _apply_cost_estimate(solicitud)
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
) -> list[HistorialEventoResponse]:
    # Resolución cross-tenant; serializamos dentro del bloque (la sesión foránea
    # se cierra al salir y el historial quedaría detached).
    async with _open_solicitud_session(
        db, solicitud_id, current_user,
        current_cliente_id, current_tecnico_id, current_taller_id,
    ) as (solicitud, _session, _usuario_id, cliente_id, tecnico_id, taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        validate_request_access(current_user, cliente_id, tecnico_id, taller_id, solicitud)
        return [
            HistorialEventoResponse.model_validate(evento)
            for evento in sorted(solicitud.historial, key=lambda item: item.fecha_evento, reverse=True)
        ]


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
    # Resolución cross-tenant: los candidatos (talleres/técnicos) se buscan en el
    # tenant DONDE VIVE la solicitud, por eso usamos `session` y no `db`.
    async with _open_solicitud_session(
        db, solicitud_id, current_user,
        current_cliente_id, current_tecnico_id, current_taller_id,
    ) as (solicitud, session, _usuario_id, cliente_id, tecnico_id, taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        validate_request_access(current_user, cliente_id, tecnico_id, taller_id, solicitud)
        talleres = await _get_candidate_workshops(session, solicitud, radio_km)
        tecnicos = await _get_candidate_technicians(session, solicitud, radio_km)
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
            servicio_unico=solicitud.servicio_demanda,
        )


# ─── /talleres-con-presupuesto ─────────────────────────────────────────
# Cache en memoria del cálculo por solicitud. TTL 5 minutos. Cada tenant
# vive en su propio proceso (o al menos su propia sesión de DB) así que
# este cache no necesita ser tenant-aware. Mantenemos la entrada por
# (tenant_key, solicitud_id, radio_km) para que el cliente pueda variar el
# radio sin mezclar respuestas entre tenants distintos.
_PRESUPUESTO_CACHE: dict[tuple[str, int, float], tuple[float, "TalleresConPresupuestoResponse"]] = {}
_PRESUPUESTO_TTL_S = 300.0


def _build_taller_con_presupuesto(
    solicitud: Solicitud,
    taller: Taller,
    distancia_km: float,
    eta_min: int | None,
) -> TallerConPresupuestoResponse:
    """
    Arma el dict completo de un taller para el cliente: distancia, ETA,
    presupuesto con descuento, match de especialización y score híbrido.

    Score híbrido (0..1):
      0.40 * cercania     — taller más cerca es mejor
      0.25 * especializacion (0 o 1)
      0.20 * descuento_marca (0 o 1)
      0.15 * rating_normalizado

    Esto sesga la recomendación a cercanía pero premia talleres
    especializados y con descuento por marca.
    """
    services_str = taller.servicios or ""
    services = [s for s in services_str.split("|") if s]
    match_especializacion, matched = _keyword_matches_for_workshop(solicitud, services)

    # Marca del vehículo para evaluar descuento. El campo puede no estar
    # cargado si la relación no se hizo selectin — sea robusto.
    marca_vehiculo = solicitud.vehiculo.marca if solicitud.vehiculo else None
    dano = solicitud.categoria_dano or "general"

    presupuesto_estimado = calcular_presupuesto_estimado(
        dano_categoria=dano,
        tarifas_base=taller.tarifas_base or {},
        descuentos_marca=taller.descuentos_marca or {},
        marca_vehiculo=marca_vehiculo,
    )

    # Sobrescribir con el 15% global por marca_asociada si aplica — gana
    # sobre los descuentos por marca específicos (es una promo del taller).
    marca_asociada_pct = descuento_por_marca_asociada(taller.marca_asociada, marca_vehiculo)
    aplico_marca_asociada = marca_asociada_pct is not None
    motivo_dto = None
    descuento_pct = presupuesto_estimado.descuento_porcentaje_aplicado
    if aplico_marca_asociada:
        descuento_pct = marca_asociada_pct
        motivo_dto = f"Marca asociada del taller ({taller.marca_asociada})"
    elif descuento_pct is not None and marca_vehiculo:
        motivo_dto = f"Descuento por marca {marca_vehiculo.upper()}"

    monto_base = (
        (presupuesto_estimado.presupuesto_min + presupuesto_estimado.presupuesto_max) / 2
        if (presupuesto_estimado.presupuesto_min is not None and presupuesto_estimado.presupuesto_max is not None)
        else (presupuesto_estimado.presupuesto_min or presupuesto_estimado.presupuesto_max or 0.0)
    )
    monto_final = (
        round(monto_base * (1 - descuento_pct / 100.0), 2)
        if descuento_pct is not None
        else round(monto_base, 2)
    )
    rango_min = (
        round((presupuesto_estimado.presupuesto_min or monto_base) * (1 - (descuento_pct or 0) / 100.0), 2)
    )
    rango_max = (
        round((presupuesto_estimado.presupuesto_max or monto_base) * (1 - (descuento_pct or 0) / 100.0), 2)
    )

    # ── Cross-check con la estimación IA ───────────────────────────────
    # Si la solicitud tiene `costo_estimado` (la IA OpenAI lo persistió al
    # subir la foto), comparamos el precio del taller contra la mediana
    # IA. Si difieren > 80% marcamos el taller para que la UI lo destaque
    # con un aviso "presupuesto fuera del rango esperado". No bloqueamos
    # — el cliente decide. Si la solicitud está abandonada o IA falló,
    # `costo_estimado` es None y este bloque es no-op.
    # getattr defensivo: tests con SimpleNamespace u objetos parciales pueden
    # no tener estas columnas. En esos casos, no hay cross-check (correcto).
    ia_min_val = getattr(solicitud, "costo_estimado_min", None)
    ia_max_val = getattr(solicitud, "costo_estimado_max", None)
    ia_prob_val = getattr(solicitud, "costo_estimado", None)
    diverge_pct: float | None = None
    requiere_revision_taller = False
    if ia_prob_val and ia_prob_val > 0 and monto_final > 0:
        diff = abs(monto_final - float(ia_prob_val)) / float(ia_prob_val)
        diverge_pct = round(diff * 100.0, 1)
        if diff > 0.8:
            requiere_revision_taller = True

    # Score híbrido — cercanía cap a 35 km, normalizada a [0,1]
    cercania_norm = max(0.0, min(1.0, 1.0 - (distancia_km / 35.0)))
    rating_norm = max(0.0, min(1.0, (taller.rating_promedio or 0.0) / 5.0))
    score = round(
        0.40 * cercania_norm
        + 0.25 * (1.0 if match_especializacion else 0.0)
        + 0.20 * (1.0 if aplico_marca_asociada else 0.0)
        + 0.15 * rating_norm,
        3,
    )

    # Texto humano para "por qué te recomiendo este taller"
    motivos: list[str] = []
    if distancia_km <= 5:           motivos.append("Cerca")
    elif distancia_km <= 15:        motivos.append("Distancia aceptable")
    if match_especializacion:       motivos.append("Especializado en tu daño")
    if aplico_marca_asociada:       motivos.append(f"{int(marca_asociada_pct)}% dto por marca")
    if (taller.rating_promedio or 0) >= 4.5: motivos.append("Rating excelente")
    if requiere_revision_taller:
        motivos.append("⚠ presupuesto inusual vs estimación IA")
    motivo = " · ".join(motivos) if motivos else "Disponible"

    return TallerConPresupuestoResponse(
        taller_id=taller.id,
        nombre=taller.nombre,
        direccion=taller.direccion,
        lat=taller.latitud,
        lng=taller.longitud,
        distancia_km=round(distancia_km, 2),
        eta_min=eta_min,
        rating_promedio=round(taller.rating_promedio or 0.0, 2),
        capacidad=taller.capacidad,
        disponible=taller.disponible,
        match_especializacion=match_especializacion,
        marca_asociada_descuento=aplico_marca_asociada,
        presupuesto=TallerPresupuestoBreakdown(
            monto_base=round(monto_base, 2),
            descuento_pct=descuento_pct,
            monto_final=monto_final,
            moneda=solicitud.moneda_costo or "BOB",
            rango_min=rango_min,
            rango_max=rango_max,
            tiempo_horas=presupuesto_estimado.tiempo_reparacion_horas,
            motivo_descuento=motivo_dto,
            estimacion_ia_min=float(ia_min_val) if ia_min_val else None,
            estimacion_ia_max=float(ia_max_val) if ia_max_val else None,
            estimacion_ia_prob=float(ia_prob_val) if ia_prob_val else None,
            diverge_ia_pct=diverge_pct,
            requiere_revision=requiere_revision_taller,
        ),
        score=score,
        motivo=motivo,
    )


@router.get(
    "/{solicitud_id:int}/talleres-con-presupuesto",
    response_model=TalleresConPresupuestoResponse,
)
async def list_workshops_with_budget(
    solicitud_id: int,
    radio_km: float = Query(default=25.0, gt=0, le=200),
    limite: int = Query(default=10, gt=0, le=50),
    refresh: bool = Query(default=False, description="Ignorar el caché de 5 min"),
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> TalleresConPresupuestoResponse:
    """
    Devuelve los talleres del tenant disponibles para esta solicitud, cada uno
    con su presupuesto estimado, ETA Mapbox y score híbrido de recomendación.

    Es el endpoint principal del flujo cliente↔taller-directo: la app móvil
    lo llama después de crear la solicitud para poblar el mapa de selección.

    Caché:
      Los cálculos son por solicitud + radio. Cachean 5 min para evitar
      recalcular Mapbox / presupuesto a cada interacción del usuario.
      ``refresh=true`` fuerza el recálculo.
    """
    # La solicitud puede vivir en el tenant del servicio (gomería→llaneros, …) y
    # no en el tenant de login del cliente. Resolvemos cruzando tenants y usamos
    # ESA sesión para listar los talleres (así los talleres son los del tenant
    # que de verdad atiende la solicitud).
    async with _open_solicitud_session(
        db,
        solicitud_id,
        current_user,
        current_cliente_id,
        current_tecnico_id,
        current_taller_id,
    ) as (solicitud, session, _usuario_id, cliente_id, tecnico_id, taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        validate_request_access(current_user, cliente_id, tecnico_id, taller_id, solicitud)

        # Cache hit?
        tenant_key = session.info.get("tenant_key", settings.default_tenant or "default")
        cache_key = (tenant_key, solicitud_id, round(radio_km, 2))
        if not refresh:
            cached = _PRESUPUESTO_CACHE.get(cache_key)
            if cached and (time.monotonic() - cached[0]) < _PRESUPUESTO_TTL_S:
                return cached[1]

        # Cargar talleres disponibles del tenant donde vive la solicitud
        result = await session.execute(select(Taller).where(Taller.disponible.is_(True)))
        talleres_db = result.scalars().all()

        # Filtrar por radio y enriquecer
        items: list[TallerConPresupuestoResponse] = []
        for taller in talleres_db:
            distancia = calcular_distancia_km(
                solicitud.latitud_incidente,
                solicitud.longitud_incidente,
                taller.latitud,
                taller.longitud,
            )
            if distancia > radio_km:
                continue
            # ETA real vía Mapbox driving — si falla, dejamos null y el cliente
            # puede estimar visualmente con la distancia.
            eta_min: int | None = None
            try:
                ruta = await route_driving(
                    origen_lat=solicitud.latitud_incidente,
                    origen_lon=solicitud.longitud_incidente,
                    destino_lat=taller.latitud,
                    destino_lon=taller.longitud,
                )
                eta_min = int(round(ruta.duration_min))
            except Exception:
                # Fallback: estimación basada en distancia + política de velocidad.
                try:
                    eta_min = int(round(estimate_eta_minutes(distancia)))
                except Exception:
                    eta_min = None

            items.append(_build_taller_con_presupuesto(solicitud, taller, distancia, eta_min))

        # Ordenar por score descendente, distancia ascendente
        items.sort(key=lambda t: (-t.score, t.distancia_km))
        items = items[:limite]

        mensaje: str | None = None
        if not items:
            mensaje = "No hay talleres disponibles en este radio. Intenta ampliar la búsqueda o cambiar a 'Modo emergencia' con un operador."

        response = TalleresConPresupuestoResponse(
            solicitud_id=solicitud.id,
            radio_km=radio_km,
            total=len(items),
            talleres=items,
            cached_at=datetime.now(timezone.utc).isoformat(),
            mensaje=mensaje,
        )

        # Guardar en caché (sólo si hubo resultados)
        if items:
            _PRESUPUESTO_CACHE[cache_key] = (time.monotonic(), response)

        return response


@router.get("/{solicitud_id:int}/seguimiento", response_model=SolicitudSeguimientoResponse)
async def get_request_tracking(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> SolicitudSeguimientoResponse:
    # Resolución cross-tenant: el seguimiento se arma dentro del bloque para que
    # las relaciones (técnico/taller) sigan adjuntas a una sesión viva.
    async with _open_solicitud_session(
        db, solicitud_id, current_user,
        current_cliente_id, current_tecnico_id, current_taller_id,
    ) as (solicitud, _session, _usuario_id, cliente_id, tecnico_id, taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        validate_request_access(current_user, cliente_id, tecnico_id, taller_id, solicitud)
        return await _build_tracking_response(solicitud)


@router.put("/{solicitud_id:int}/asignar", response_model=SolicitudResponse)
async def assign_request(
    solicitud_id: int,
    payload: SolicitudAsignar,
    current_user: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
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
        tecnicos_taller = (
            await db.execute(select(Tecnico).where(Tecnico.taller_id == taller.id, Tecnico.disponibilidad.is_(True)))
        ).scalars().all()
        candidatos_taller = [
            (match, item)
            for item in tecnicos_taller
            if (match := calcular_match_tecnico(solicitud, item, max_radio_km=25)) is not None
        ]
        if candidatos_taller:
            candidatos_taller.sort(key=lambda pair: (-pair[0].score, pair[0].distancia_km))
            tecnico = candidatos_taller[0][1]
    if tecnico is None and taller is None:
        candidatos_globales = await _get_candidate_technicians(db, solicitud, radio_km=25)
        if candidatos_globales:
            tecnico = await db.get(Tecnico, candidatos_globales[0].id)
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
    servicio = await _ensure_on_demand_service(
        db,
        solicitud,
        latitud_cliente=solicitud.servicio_demanda.latitud_cliente if solicitud.servicio_demanda else solicitud.latitud_incidente,
        longitud_cliente=solicitud.servicio_demanda.longitud_cliente if solicitud.servicio_demanda else solicitud.longitud_incidente,
    )
    servicio.taller_id = taller_id
    servicio.tecnico_id = tecnico.id if tecnico else None
    servicio.estado = "PROPUESTA_ENVIADA" if tecnico else "SIN_TECNICO"
    if tecnico:
        match = calcular_match_tecnico(solicitud, tecnico, max_radio_km=25)
        if match is not None:
            servicio.cobertura_tecnico_km = tecnico.radio_cobertura_km
            servicio.distancia_asignacion_km = match.distancia_km
            servicio.eta_estimado_min = match.eta_min
            servicio.score_matching = match.score
            servicio.match_especialidad = match.match_especialidad
            servicio.detalle_matching = match.detalle

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
                f"La solicitud #{solicitud.id} ({_tipo_incidente_label(solicitud)}) fue propuesta al taller {taller.nombre} con el técnico {tecnico.nombre}. Debe ser aprobada por el cliente."
                if tecnico and taller
                else f"La solicitud #{solicitud.id} ({_tipo_incidente_label(solicitud)}) fue propuesta al taller {taller.nombre}. No hay técnico disponible todavía y se notificará cuando haya cobertura."
            )
            if taller
            else f"La solicitud #{solicitud.id} fue enviada a asignación operativa."
        ),
        "ASIGNACION_TALLER",
        deep_link=f"/solicitudes/{solicitud.id}",
    )
    if tecnico is None and taller is not None:
        await _notify_users(
            db,
            await _get_operador_user_ids(db),
            "Sin técnicos disponibles",
            f"La solicitud #{solicitud.id} tiene taller propuesto pero aún no cuenta con técnico disponible.",
            "SIN_TECNICO_DISPONIBLE",
            deep_link=f"/solicitudes/{solicitud.id}",
        )
    await db.commit()

    result = await _load_request_with_relations(db, solicitud.id)
    if not result:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return result


@router.put("/{solicitud_id:int}/seleccionar-taller", response_model=SolicitudResponse)
async def select_workshop_for_request(
    solicitud_id: int,
    payload: SolicitudSeleccionTallerRequest,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> SolicitudResponse:
    # La solicitud puede vivir en el tenant del servicio (gomería→llaneros, …).
    # Resolvemos cruzando tenants y operamos TODO (taller, servicio, historial,
    # commit, broadcast) sobre ESA sesión: `db` se reasigna a la sesión resuelta.
    # `usuario_id` es el id del usuario en ESE tenant (las identidades son
    # por-tenant), y se usa para HistorialEvento / notificaciones del cliente sin
    # romper la FK (current_user.id es el id del tenant de login, no sirve aquí).
    async with _open_solicitud_session(
        db,
        solicitud_id,
        current_user,
        current_cliente_id,
        current_tecnico_id,
        current_taller_id,
    ) as (solicitud, db, usuario_id, current_cliente_id, current_tecnico_id, current_taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        validate_request_access(current_user, current_cliente_id, current_tecnico_id, current_taller_id, solicitud)

        taller = await db.get(Taller, payload.taller_id)
        if not taller:
            raise HTTPException(status_code=404, detail="Taller no encontrado")
        if not taller.disponible:
            raise HTTPException(status_code=400, detail="El taller seleccionado no está disponible")

        try:
            ruta = await route_driving(
                origen_lat=payload.origen_lat,
                origen_lon=payload.origen_lon,
                destino_lat=taller.latitud,
                destino_lon=taller.longitud,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail="No se pudo calcular la ruta en este momento") from exc

        # ── Transición de estado al flujo cliente↔taller-directo ────────────────
        # Si la solicitud está REGISTRADA o RECHAZADA_TALLER (cliente re-eligió
        # tras un rechazo), avanza a PROPUESTA_TALLER. Si ya está en otro estado
        # (legacy flow donde el operador asignó), respeta el estado actual y
        # solo asocia el taller.
        estado_anterior_nombre = solicitud.estado.nombre if solicitud.estado else "SIN_ESTADO"
        propuesta_state = None
        if estado_anterior_nombre in {"REGISTRADA", "RECHAZADA_TALLER"}:
            propuesta_state = await _get_estado_por_nombre(db, "PROPUESTA_TALLER")
            solicitud.estado_id = propuesta_state.id

        solicitud.taller_id = taller.id
        solicitud.presupuesto_aceptado = payload.presupuesto_aceptado
        solicitud.ruta_osrm = ruta.geometry
        solicitud.ruta_distancia_km = ruta.distance_km
        solicitud.ruta_eta_min = int(round(ruta.duration_min))
        # El cliente aprueba implícitamente al seleccionar — ya no necesita un
        # paso aparte de aprobación como en el flujo del operador.
        solicitud.cliente_aprobada = True
        solicitud.cliente_aprobacion_fecha = datetime.now(timezone.utc)
        servicio = await _ensure_on_demand_service(
            db,
            solicitud,
            latitud_cliente=payload.origen_lat,
            longitud_cliente=payload.origen_lon,
        )
        servicio.taller_id = taller.id
        servicio.estado = "TALLER_PRESELECCIONADO"
        servicio.distancia_asignacion_km = ruta.distance_km
        servicio.eta_estimado_min = int(round(ruta.duration_min))
        servicio.detalle_matching = "Taller elegido por el cliente con geolocalización validada."

        estado_nuevo_nombre = propuesta_state.nombre if propuesta_state else estado_anterior_nombre
        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_anterior_nombre,
                estado_nuevo=estado_nuevo_nombre,
                observacion=f"Taller seleccionado por el usuario: {taller.nombre}. Ruta calculada por Mapbox Directions.",
                usuario_id=usuario_id,
            )
        )

        # ── Auto-aceptación ─────────────────────────────────────────────────────
        # Si el taller activó "aceptar automáticamente", la propuesta no espera
        # respuesta manual: avanza de inmediato PROPUESTA_TALLER → ASIGNADA con la
        # misma lógica que respond_workshop_assignment (estado, servicio, contador).
        auto_aceptada = bool(propuesta_state) and taller.acepta_automaticamente
        tecnico_auto: Tecnico | None = None
        if auto_aceptada:
            estado_anterior_auto = propuesta_state.nombre if propuesta_state is not None else estado_anterior_nombre
            estado_asignada = await _get_estado_por_nombre(db, "ASIGNADA")
            solicitud.estado_id = estado_asignada.id
            solicitud.fecha_asignacion = datetime.now(timezone.utc)
            solicitud.taller_rechazos_consecutivos = 0
            servicio.estado = "ACEPTADO_TALLER"
            estado_nuevo_nombre = estado_asignada.nombre
            db.add(
                HistorialEvento(
                    solicitud_id=solicitud.id,
                    estado_anterior=estado_anterior_auto,
                    estado_nuevo=estado_asignada.nombre,
                    observacion=(
                        f"{taller.nombre} aceptó automáticamente la propuesta "
                        "(aceptación automática activada en la configuración del taller)."
                    ),
                    usuario_id=usuario_id,
                )
            )

            # Auto-asignar un técnico disponible del taller. Sin técnico, el
            # seguimiento queda en "La solicitud aún no tiene un técnico confirmado"
            # y no hay ruta ni ETA (ver _build_tracking_response). Elegimos el mejor
            # match por cercanía/score, con fallback al primer técnico disponible.
            if solicitud.tecnico_id is None:
                tecnicos_taller = (
                    await db.execute(
                        select(Tecnico).where(
                            Tecnico.taller_id == taller.id,
                            Tecnico.disponibilidad.is_(True),
                        )
                    )
                ).scalars().all()
                candidatos = [
                    (match, item)
                    for item in tecnicos_taller
                    if (match := calcular_match_tecnico(solicitud, item, max_radio_km=25)) is not None
                ]
                match_auto = None
                if candidatos:
                    candidatos.sort(key=lambda pair: (-pair[0].score, pair[0].distancia_km))
                    match_auto, tecnico_auto = candidatos[0]
                elif tecnicos_taller:
                    tecnico_auto = tecnicos_taller[0]
                if tecnico_auto is not None:
                    solicitud.tecnico_id = tecnico_auto.id
                    tecnico_auto.disponibilidad = False
                    servicio.tecnico_id = tecnico_auto.id
                    servicio.cobertura_tecnico_km = tecnico_auto.radio_cobertura_km
                    if match_auto is not None:
                        servicio.distancia_asignacion_km = match_auto.distancia_km
                        servicio.eta_estimado_min = match_auto.eta_min
                        servicio.score_matching = match_auto.score
                        servicio.match_especialidad = match_auto.match_especialidad
                        servicio.detalle_matching = match_auto.detalle
                    db.add(
                        HistorialEvento(
                            solicitud_id=solicitud.id,
                            estado_anterior=estado_asignada.nombre,
                            estado_nuevo=estado_asignada.nombre,
                            observacion=f"Técnico {tecnico_auto.nombre} asignado automáticamente por el taller.",
                            usuario_id=usuario_id,
                        )
                    )

        # Notificaciones — separamos cliente y taller porque sus deep_links
        # son distintos (cliente → detalle de su solicitud, taller → bandeja).
        # El recipiente del cliente es `usuario_id` (su id en el tenant del
        # servicio); taller/técnico ya traen su user_id de ESE tenant.
        if auto_aceptada:
            # El taller ya aceptó: avisamos al cliente igual que en la aceptación
            # manual. No mandamos la propuesta a la bandeja del taller.
            await _notify_users(
                db,
                [usuario_id],
                f"{taller.nombre} aceptó tu solicitud",
                f"El taller {taller.nombre} aceptó automáticamente tu solicitud #{solicitud.id} y está preparándose.",
                "TALLER_ACEPTO",
                deep_link=f"/solicitudes/{solicitud.id}",
            )
            # Técnico auto-asignado: avisar para que comparta su ubicación y arranque.
            if tecnico_auto is not None and tecnico_auto.user_id:
                await _notify_users(
                    db,
                    [tecnico_auto.user_id],
                    "Nueva solicitud asignada",
                    f"El taller {taller.nombre} te asignó la solicitud #{solicitud.id} ({_tipo_incidente_label(solicitud)}). Comparte tu ubicación para iniciar.",
                    "TECNICO_ASIGNADO",
                    deep_link=f"/solicitudes/{solicitud.id}",
                )
        elif propuesta_state:
            # Cliente: confirmación de que la propuesta se envió.
            await _notify_users(
                db,
                [usuario_id],
                "Esperando al taller",
                f"Enviamos tu solicitud al taller {taller.nombre}. Te avisaremos cuando responda.",
                "PROPUESTA_TALLER_CLIENTE",
                deep_link=f"/solicitudes/{solicitud.id}",
            )
            # Taller: nueva propuesta entrante, debe aceptar o rechazar.
            if taller.user_id:
                await _notify_users(
                    db,
                    [taller.user_id],
                    "Nueva solicitud disponible",
                    f"El cliente eligió tu taller para la solicitud #{solicitud.id} ({_tipo_incidente_label(solicitud)}). Tienes que aceptar o rechazar.",
                    "PROPUESTA_TALLER",
                    deep_link="/taller/inbox",
                )
        else:
            # Flujo legacy — un solo mensaje al cliente + taller con deep_link al detalle.
            notify_ids: list[int] = [usuario_id]
            if taller.user_id:
                notify_ids.append(taller.user_id)
            await _notify_users(
                db,
                notify_ids,
                "Taller seleccionado",
                f"La solicitud #{solicitud.id} fue asociada al taller {taller.nombre}.",
                "TALLER_SELECCIONADO",
                deep_link=f"/solicitudes/{solicitud.id}",
            )
        await db.commit()
        # Push del nuevo estado al WebSocket — el cliente espera "PROPUESTA_TALLER"
        # para mostrar "Esperando aceptación del taller…" y el taller para
        # mostrar la notificación en su Inbox.
        if propuesta_state:
            tenant = db.info.get("tenant_key", "default")
            await _broadcast_state_change(
                tenant, solicitud.id, estado_nuevo_nombre, taller_id=taller.id,
            )

        result = await _load_request_with_relations(db, solicitud.id)
        if not result:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        return SolicitudResponse.model_validate(result)


@router.put("/{solicitud_id:int}/ruta", response_model=SolicitudResponse)
async def refresh_request_route(
    solicitud_id: int,
    payload: SolicitudActualizarRutaRequest,
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

    if solicitud.taller_id is None or solicitud.taller is None:
        raise HTTPException(status_code=400, detail="La solicitud no tiene taller seleccionado")

    try:
        ruta = await route_driving(
            origen_lat=payload.origen_lat,
            origen_lon=payload.origen_lon,
            destino_lat=solicitud.taller.latitud,
            destino_lon=solicitud.taller.longitud,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo calcular la ruta en este momento") from exc

    solicitud.ruta_osrm = ruta.geometry
    solicitud.ruta_distancia_km = ruta.distance_km
    solicitud.ruta_eta_min = int(round(ruta.duration_min))
    if solicitud.servicio_demanda:
        solicitud.servicio_demanda.latitud_cliente = payload.origen_lat
        solicitud.servicio_demanda.longitud_cliente = payload.origen_lon
        solicitud.servicio_demanda.distancia_asignacion_km = ruta.distance_km
        solicitud.servicio_demanda.eta_estimado_min = int(round(ruta.duration_min))
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
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> SolicitudResponse:
    async with _open_solicitud_session(
        db, solicitud_id, current_user,
        current_cliente_id, current_tecnico_id, current_taller_id,
    ) as (solicitud, db, usuario_id, current_cliente_id, current_tecnico_id, current_taller_id):
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
            _reset_on_demand_service(solicitud.servicio_demanda)
            db.add(
                HistorialEvento(
                    solicitud_id=solicitud.id,
                    estado_anterior=estado_actual.nombre,
                    estado_nuevo=estado_registrada.nombre,
                    observacion="La propuesta expiró y volvió a cola operativa",
                    usuario_id=usuario_id,
                )
            )
            await db.commit()
            result = await _load_request_with_relations(db, solicitud.id)
            if not result:
                raise HTTPException(status_code=404, detail="Solicitud no encontrada")
            return SolicitudResponse.model_validate(result)
        solicitud.cliente_aprobada = payload.aprobada
        solicitud.cliente_aprobacion_observacion = payload.observacion
        solicitud.cliente_aprobacion_fecha = datetime.now(timezone.utc)
        cliente = await db.get(Cliente, solicitud.cliente_id)
        operador_ids = await _get_operador_user_ids(db)
        if payload.aprobada:
            if solicitud.servicio_demanda:
                solicitud.servicio_demanda.estado = "APROBADO_CLIENTE"
            db.add(
                HistorialEvento(
                    solicitud_id=solicitud.id,
                    estado_anterior=estado_actual.nombre,
                    estado_nuevo=estado_actual.nombre,
                    observacion=f"Cliente aprobó la propuesta: {payload.observacion}",
                    usuario_id=usuario_id,
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
                deep_link=f"/solicitudes/{solicitud.id}",
            )
        else:
            estado_registrada = await _get_estado_por_nombre(db, "REGISTRADA")
            solicitud.estado_id = estado_registrada.id
            if solicitud.tecnico_id and solicitud.tecnico:
                solicitud.tecnico.disponibilidad = True
            solicitud.tecnico_id = None
            previous_taller_name = solicitud.taller.nombre if solicitud.taller else "sin taller"
            solicitud.taller_id = None
            _reset_on_demand_service(solicitud.servicio_demanda)
            db.add(
                HistorialEvento(
                    solicitud_id=solicitud.id,
                    estado_anterior=estado_actual.nombre,
                    estado_nuevo=estado_registrada.nombre,
                    observacion=f"Cliente rechazó la propuesta de {previous_taller_name}: {payload.observacion}",
                    usuario_id=usuario_id,
                )
            )
            await _notify_users(
                db,
                operador_ids,
                "Cliente rechazó la propuesta",
                f"La solicitud #{solicitud.id} requiere una nueva asignación operativa.",
                "ASIGNACION_RECHAZADA_CLIENTE",
                deep_link=f"/solicitudes/{solicitud.id}",
            )
        if cliente and cliente.user_id:
            await _notify_users(
                db,
                [cliente.user_id],
                "Respuesta registrada",
                f"Tu respuesta sobre la propuesta de la solicitud #{solicitud.id} fue guardada.",
                "RESPUESTA_PROPUESTA_CLIENTE",
                deep_link=f"/solicitudes/{solicitud.id}",
            )
        await db.commit()
        result = await _load_request_with_relations(db, solicitud.id)
        if not result:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        return SolicitudResponse.model_validate(result)


@router.put("/{solicitud_id:int}/respuesta-taller", response_model=SolicitudResponse)
async def respond_workshop_assignment(
    solicitud_id: int,
    payload: SolicitudResponderAsignacionRequest,
    current_user: User = Depends(get_current_user),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    """
    El taller responde a una propuesta del cliente.

    Dos modos:

      (a) Flujo nuevo cliente↔taller-directo (estado PROPUESTA_TALLER):
          - aceptar → ASIGNADA + reset contador rechazos + notify cliente
          - rechazar → RECHAZADA_TALLER + counter++ + clear taller_id
            (si counter llega a 3, escalar a operadores)

      (b) Flujo legacy (estado distinto a PROPUESTA_TALLER): mantiene el
          comportamiento previo donde el cliente debía aprobar primero.
    """
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if "TALLER" not in get_role_names(current_user) or solicitud.taller_id != current_taller_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el taller asignado puede responder")
    estado_actual = await db.get(EstadoSolicitud, solicitud.estado_id)
    if not estado_actual:
        raise HTTPException(status_code=404, detail="Estado actual no encontrado")
    cliente = await db.get(Cliente, solicitud.cliente_id)

    # ── Branch del flujo nuevo (PROPUESTA_TALLER) ───────────────────────────
    if estado_actual.nombre == "PROPUESTA_TALLER":
        taller_nombre = solicitud.taller.nombre if solicitud.taller else "Taller"
        if payload.aceptada:
            estado_asignada = await _get_estado_por_nombre(db, "ASIGNADA")
            solicitud.estado_id = estado_asignada.id
            solicitud.fecha_asignacion = datetime.now(timezone.utc)
            # Reset del contador — los rechazos anteriores ya no cuentan
            # para escalamiento porque alguien finalmente aceptó.
            solicitud.taller_rechazos_consecutivos = 0
            if solicitud.servicio_demanda:
                solicitud.servicio_demanda.estado = "ACEPTADO_TALLER"
            db.add(
                HistorialEvento(
                    solicitud_id=solicitud.id,
                    estado_anterior=estado_actual.nombre,
                    estado_nuevo=estado_asignada.nombre,
                    observacion=f"{taller_nombre} aceptó la propuesta: {payload.observacion}",
                    usuario_id=current_user.id,
                )
            )
            # Notify cliente — push + WS broadcast
            if cliente and cliente.user_id:
                await _notify_users(
                    db,
                    [cliente.user_id],
                    f"{taller_nombre} aceptó tu solicitud",
                    f"El taller {taller_nombre} aceptó tu solicitud #{solicitud.id} y está preparándose.",
                    "TALLER_ACEPTO",
                    deep_link=f"/solicitudes/{solicitud.id}",
                )
            await db.commit()
            tenant = db.info.get("tenant_key", "default")
            await _broadcast_state_change(
                tenant, solicitud.id, estado_asignada.nombre,
                taller_id=solicitud.taller_id,
            )
            result = await _load_request_with_relations(db, solicitud.id)
            if not result:
                raise HTTPException(status_code=404, detail="Solicitud no encontrada")
            return result

        # ── Rechazo ─────────────────────────────────────────────────────
        estado_rechazada = await _get_estado_por_nombre(db, "RECHAZADA_TALLER")
        solicitud.estado_id = estado_rechazada.id
        # Incrementar el contador de rechazos consecutivos
        solicitud.taller_rechazos_consecutivos = (
            (solicitud.taller_rechazos_consecutivos or 0) + 1
        )
        rechazos = solicitud.taller_rechazos_consecutivos
        # Limpiar el taller para que el cliente pueda re-elegir.
        rejected_taller_id = solicitud.taller_id
        rejected_taller_nombre = taller_nombre
        solicitud.taller_id = None
        solicitud.cliente_aprobada = None
        solicitud.presupuesto_aceptado = None
        if solicitud.servicio_demanda:
            _reset_on_demand_service(solicitud.servicio_demanda)

        db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre,
                estado_nuevo=estado_rechazada.nombre,
                observacion=(
                    f"{rejected_taller_nombre} rechazó la propuesta "
                    f"(#{rechazos} rechazo consecutivo): {payload.observacion}"
                ),
                usuario_id=current_user.id,
            )
        )

        # Notify cliente: tiene que elegir otro taller
        if cliente and cliente.user_id:
            await _notify_users(
                db,
                [cliente.user_id],
                "Taller no disponible",
                (
                    f"El taller {rejected_taller_nombre} no puede atender tu solicitud "
                    f"#{solicitud.id}. Elige otro taller desde la app."
                ),
                "TALLER_RECHAZO",
                deep_link=f"/solicitudes/{solicitud.id}",
            )

        # Escalamiento: 3 rechazos consecutivos → alertar a operadores
        if rechazos >= 3:
            operador_ids = await _get_operador_user_ids(db)
            if operador_ids:
                await _notify_users(
                    db,
                    operador_ids,
                    "Solicitud necesita asignación manual",
                    (
                        f"La solicitud #{solicitud.id} acumuló {rechazos} rechazos "
                        "consecutivos de talleres. Considera asignar manualmente."
                    ),
                    "ESCALAMIENTO_RECHAZOS",
                    deep_link=f"/solicitudes/{solicitud.id}",
                )

        await db.commit()
        tenant = db.info.get("tenant_key", "default")
        await _broadcast_state_change(
            tenant, solicitud.id, estado_rechazada.nombre,
            taller_id=rejected_taller_id,
        )
        result = await _load_request_with_relations(db, solicitud.id)
        if not result:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        return result

    # ── Branch legacy (cliente aprueba propuesta del operador) ──────────────
    if solicitud.cliente_aprobada is False:
        raise HTTPException(status_code=400, detail="La propuesta aún debe ser aprobada por el cliente")
    if _is_client_approval_expired(solicitud):
        raise HTTPException(status_code=400, detail="La propuesta expiró y requiere una nueva asignación")
    if payload.aceptada:
        if solicitud.servicio_demanda:
            solicitud.servicio_demanda.estado = "ACEPTADO_TALLER"
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
                if solicitud.servicio_demanda:
                    match = calcular_match_tecnico(solicitud, tecnico, max_radio_km=25)
                    solicitud.servicio_demanda.tecnico_id = tecnico.id
                    solicitud.servicio_demanda.cobertura_tecnico_km = tecnico.radio_cobertura_km
                    solicitud.servicio_demanda.estado = "PROPUESTA_ENVIADA"
                    if match is not None:
                        solicitud.servicio_demanda.distancia_asignacion_km = match.distancia_km
                        solicitud.servicio_demanda.eta_estimado_min = match.eta_min
                        solicitud.servicio_demanda.score_matching = match.score
                        solicitud.servicio_demanda.match_especialidad = match.match_especialidad
                        solicitud.servicio_demanda.detalle_matching = match.detalle
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
                    deep_link=f"/solicitudes/{solicitud.id}",
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
        _reset_on_demand_service(solicitud.servicio_demanda)
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
            deep_link=f"/solicitudes/{solicitud.id}",
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
            deep_link=f"/solicitudes/{solicitud.id}",
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
        if solicitud.servicio_demanda:
            solicitud.servicio_demanda.estado = "EN_CAMINO"
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
                deep_link=f"/solicitudes/{solicitud.id}",
            )
    else:
        estado_registrada = await _get_estado_por_nombre(db, "REGISTRADA")
        solicitud.estado_id = estado_registrada.id
        solicitud.tecnico_id = None
        solicitud.cliente_aprobada = None
        solicitud.taller_id = None
        tecnico.disponibilidad = True
        _reset_on_demand_service(solicitud.servicio_demanda)
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
            deep_link=f"/solicitudes/{solicitud.id}",
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
        deep_link=f"/solicitudes/{solicitud.id}",
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
    specialized_tags = _specialize_diagnostic_tags(
        tipo_incidente=solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else None,
        descripcion=merged_text,
        tags=triage.detected_tags,
    )
    solicitud.etiquetas_ia = _merge_ai_tags(solicitud.etiquetas_ia, specialized_tags)
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
            if transcription.requiere_revision_humana or not transcription.transcript.strip():
                # Ningún proveedor real transcribió el audio: marcamos ERROR
                # honesto en vez de guardar texto fabricado. El audio queda
                # como evidencia y puede reintentarse desde el panel.
                solicitud.transcripcion_audio = None
                solicitud.transcripcion_audio_estado = "ERROR"
                solicitud.transcripcion_audio_error = (
                    "La transcripción automática no está disponible. "
                    "El audio requiere revisión manual."
                )
                solicitud.transcripcion_audio_actualizada_en = datetime.now(timezone.utc)
                solicitud.requiere_revision_manual = True
                raise _TranscriptionUnavailable()
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
            audio_tags = _specialize_diagnostic_tags(
                tipo_incidente=solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else None,
                descripcion=transcription.transcript,
                tags=audio_triage.detected_tags,
            )
            solicitud.resumen_ia = _build_technical_diagnostic_summary(
                tipo_incidente=solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else None,
                descripcion=transcription.transcript,
                base_summary=audio_triage.summary,
                requires_manual_review=audio_triage.requires_manual_review or transcription.confidence < 0.65,
                tags=audio_tags,
            )
            solicitud.clasificacion_confianza = max(solicitud.clasificacion_confianza or 0, transcription.confidence, audio_triage.confidence)
            solicitud.etiquetas_ia = _merge_ai_tags(solicitud.etiquetas_ia, audio_tags)
            if transcription.confidence < 0.65 or audio_triage.requires_manual_review:
                solicitud.requiere_revision_manual = True

            roles = set(get_role_names(current_user))
            if "CLIENTE" in roles and solicitud.transcripcion_audio:
                snippet = solicitud.transcripcion_audio.strip().replace("\n", " ") # type: ignore
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
                    deep_link=f"/solicitudes/{solicitud.id}",
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
        except _TranscriptionUnavailable:
            # Estado ERROR honesto ya quedó registrado antes del raise.
            pass
        except Exception as exc:
            solicitud.transcripcion_audio_estado = "ERROR"
            solicitud.transcripcion_audio_error = str(exc)[:500]
            solicitud.transcripcion_audio_actualizada_en = datetime.now(timezone.utc)
    else:
        try:
            image_analysis = await analyze_image_file(
                archivo.filename or target_path.name,
                archivo.content_type,
                f"{solicitud.descripcion} {solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else ''}",
                file_bytes=content,
            )
            evidence.contenido_texto = json.dumps(
                {
                    "status": "OK",
                    "labels": image_analysis.labels,
                    "summary": image_analysis.summary,
                    "confidence": image_analysis.confidence,
                    "provider": image_analysis.provider,
                    "components": image_analysis.components,
                    "damage_zones": image_analysis.damage_zones,
                    "severity": image_analysis.severity,
                    "visual_factor": image_analysis.visual_factor,
                    "ocr_text": image_analysis.ocr_text,
                    "alt_text": image_analysis.alt_text,
                    "moderation": image_analysis.moderation,
                },
                ensure_ascii=False,
            )
            solicitud.etiquetas_ia = _merge_ai_tags(solicitud.etiquetas_ia, image_analysis.labels)
            if "choque" in image_analysis.labels or "motor" in image_analysis.labels:
                solicitud.nivel_riesgo = max(solicitud.nivel_riesgo, 4)
            # El helper centraliza: resumen_ia, proveedor_ia, confianza,
            # requiere_revision_manual, costo_estimado_*, costo_estimacion_*,
            # y registra un row en ia_audit_log. Idempotente.
            persist_image_ai_outcome(solicitud, image_analysis, db)
        except Exception as exc:
            evidence.contenido_texto = json.dumps(
                {"status": "ERROR", "error": str(exc)[:500]},
                ensure_ascii=False,
            )
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


def can_finalize_work(
    roles: set[str] | list[str],
    *,
    solicitud_tecnico_id: int | None,
    current_tecnico_id: int | None,
    solicitud_taller_id: int | None,
    current_taller_id: int | None,
) -> bool:
    """¿Puede el usuario cerrar el trabajo (registrar trabajo_terminado + costo_final)?

    Dos caminos válidos:
      - El TÉCNICO asignado a la solicitud (flujo con técnico en terreno).
      - El TALLER dueño cuando la solicitud NO tiene técnico (flujo
        "taller sin técnico": el taller cierra el trabajo de forma remota).
    """
    es_tecnico_asignado = (
        "TECNICO" in roles
        and solicitud_tecnico_id is not None
        and solicitud_tecnico_id == current_tecnico_id
    )
    es_taller_sin_tecnico = (
        "TALLER" in roles
        and solicitud_tecnico_id is None
        and solicitud_taller_id is not None
        and solicitud_taller_id == current_taller_id
    )
    return es_tecnico_asignado or es_taller_sin_tecnico


@router.put("/{solicitud_id:int}/trabajo-finalizado", response_model=SolicitudResponse)
async def finalize_request_work(
    solicitud_id: int,
    payload: SolicitudTrabajoFinalizadoRequest,
    current_user: User = Depends(get_current_user),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    roles = get_role_names(current_user)
    # Quién puede cerrar el trabajo:
    #   - El TÉCNICO asignado (flujo con técnico en terreno), o
    #   - El TALLER dueño cuando la solicitud NO tiene técnico (flujo
    #     "taller sin técnico": el taller cierra el trabajo de forma remota).
    es_tecnico_asignado = (
        "TECNICO" in roles
        and solicitud.tecnico_id is not None
        and solicitud.tecnico_id == current_tecnico_id
    )
    if not can_finalize_work(
        roles,
        solicitud_tecnico_id=solicitud.tecnico_id,
        current_tecnico_id=current_tecnico_id,
        solicitud_taller_id=solicitud.taller_id,
        current_taller_id=current_taller_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el técnico asignado o el taller responsable puede cerrar el trabajo",
        )
    estado_actual = solicitud.estado.nombre if solicitud.estado else ""
    if estado_actual != "EN_ATENCION":
        raise HTTPException(status_code=400, detail="La solicitud debe estar en atención para cerrar el trabajo")
    if solicitud.trabajo_terminado:
        raise HTTPException(status_code=400, detail="El trabajo ya fue registrado como finalizado")

    solicitud.trabajo_terminado = True
    solicitud.trabajo_terminado_en = datetime.now(timezone.utc)
    solicitud.trabajo_terminado_observacion = payload.observacion.strip()
    solicitud.costo_final = round(payload.costo_final, 2)
    solicitud.moneda_costo = "BOB"
    servicio = solicitud.servicio_demanda

    # Confirmación de ubicación: SOLO la aporta el técnico parado en el sitio.
    # Si vienen coords, validamos que coincidan con el punto del servicio. El
    # taller sin técnico cierra de forma remota (sin coords) y se omite.
    ubicacion_txt = ""
    if payload.latitud_confirmacion is not None and payload.longitud_confirmacion is not None:
        if servicio is None:
            raise HTTPException(status_code=400, detail="La solicitud no tiene un servicio bajo demanda asociado")
        distancia_confirmacion_km = calcular_distancia_km(
            servicio.latitud_servicio,
            servicio.longitud_servicio,
            payload.latitud_confirmacion,
            payload.longitud_confirmacion,
        )
        if distancia_confirmacion_km > 0.35:
            raise HTTPException(
                status_code=400,
                detail="La confirmación final de ubicación no coincide con el punto registrado del servicio",
            )
        servicio.confirmacion_ubicacion_ok = True
        servicio.latitud_confirmacion_final = payload.latitud_confirmacion
        servicio.longitud_confirmacion_final = payload.longitud_confirmacion
        servicio.distancia_confirmacion_m = round(distancia_confirmacion_km * 1000, 2)
        servicio.confirmacion_ubicacion_en = datetime.now(timezone.utc)
        ubicacion_txt = f"Ubicación validada a {servicio.distancia_confirmacion_m:.0f} m del punto del servicio. "
    if servicio is not None:
        servicio.estado = "COMPLETADO_PENDIENTE_PAGO"

    quien = "El técnico" if es_tecnico_asignado else "El taller"
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_actual,
            estado_nuevo=estado_actual,
            observacion=(
                f"Trabajo realizado. Costo final {format_bs(solicitud.costo_final)}. "
                f"{ubicacion_txt}{payload.observacion.strip()}"
            ),
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
        f"{quien} cerró el trabajo de la solicitud #{solicitud.id} con costo final {format_bs(solicitud.costo_final)}.",
        "TRABAJO_FINALIZADO",
        deep_link=f"/solicitudes/{solicitud.id}",
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
    async with _open_solicitud_session(
        db, solicitud_id, current_user,
        current_cliente_id, current_tecnico_id, current_taller_id,
    ) as (solicitud, _session, _usuario_id, cliente_id, tecnico_id, taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        validate_request_access(current_user, cliente_id, tecnico_id, taller_id, solicitud)
        return sorted(solicitud.pagos, key=lambda item: item.fecha_creacion, reverse=True)


@router.post("/{solicitud_id:int}/pago", response_model=PagoResponse, status_code=status.HTTP_201_CREATED)
async def create_request_payment(
    solicitud_id: int,
    payload: PagoCreate,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> PagoSolicitud:
    async with _open_solicitud_session(
        db, solicitud_id, current_user,
        current_cliente_id, current_tecnico_id, current_taller_id,
    ) as (solicitud, db, usuario_id, cliente_id, _tecnico_id, _taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        roles = get_role_names(current_user)
        if "CLIENTE" not in roles or solicitud.cliente_id != cliente_id:
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
                usuario_id=usuario_id,
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
                    usuario_id=usuario_id,
                )
            )
        notify_ids = [usuario_id]
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
            deep_link=f"/solicitudes/{solicitud.id}",
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
    _: User = Depends(require_roles("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR", "TALLER")),
    db: AsyncSession = Depends(get_db),
) -> TrabajoRealizadoListResponse:
    return await _fetch_trabajos_realizados(db, desde, hasta, tecnico_id, taller_id)


@router.get("/trabajos-realizados", response_model=TrabajoRealizadoListResponse)
async def list_completed_jobs_alias(
    desde: str | None = Query(default=None),
    hasta: str | None = Query(default=None),
    tecnico_id: int | None = Query(default=None),
    taller_id: int | None = Query(default=None),
    _: User = Depends(require_roles("ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR", "TALLER")),
    db: AsyncSession = Depends(get_db),
) -> TrabajoRealizadoListResponse:
    return await _fetch_trabajos_realizados(db, desde, hasta, tecnico_id, taller_id)


@router.get("/trabajos-realizados/pdf")
async def export_completed_jobs_pdf_alias(
    request: Request,
    desde: str | None = Query(default=None),
    hasta: str | None = Query(default=None),
    tecnico_id: int | None = Query(default=None),
    taller_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await export_completed_jobs_pdf(
        request=request,
        desde=desde,
        hasta=hasta,
        tecnico_id=tecnico_id,
        taller_id=taller_id,
        db=db,
    )


@router.get("/trabajos-realizados/csv")
async def export_completed_jobs_csv_alias(
    request: Request,
    desde: str | None = Query(default=None),
    hasta: str | None = Query(default=None),
    tecnico_id: int | None = Query(default=None),
    taller_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await export_completed_jobs_csv(
        request=request,
        desde=desde,
        hasta=hasta,
        tecnico_id=tecnico_id,
        taller_id=taller_id,
        db=db,
    )


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
    if not roles.intersection({"ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR", "TALLER"}):
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
    if not roles.intersection({"ADMINISTRADOR", "ADMIN_TENANT", "OPERADOR", "TALLER"}):
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
        if transcription.requiere_revision_humana or not transcription.transcript.strip():
            # Reintento sin éxito: no fabricamos texto, dejamos ERROR honesto.
            solicitud.transcripcion_audio = None
            solicitud.transcripcion_audio_estado = "ERROR"
            solicitud.transcripcion_audio_error = (
                "La transcripción automática no está disponible. "
                "El audio requiere revisión manual."
            )
            solicitud.transcripcion_audio_actualizada_en = datetime.now(timezone.utc)
            solicitud.requiere_revision_manual = True
        else:
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


@router.post("/{solicitud_id:int}/imagenes/reprocesar", response_model=SolicitudResponse)
async def retry_image_analysis(
    solicitud_id: int,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await _load_request_with_relations(db, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")

    image_evidences = [item for item in solicitud.evidencias if item.tipo == "IMAGE"]
    if not image_evidences:
        raise HTTPException(status_code=400, detail="No hay evidencias de imagen para reprocesar")

    processed = 0
    failed = 0
    best_visual_factor = 0.0
    best_summary: str | None = None
    for evidence in sorted(image_evidences, key=lambda item: item.fecha_creacion, reverse=True):
        try:
            resolved_path = _resolve_evidence_storage_path(evidence)
            if not resolved_path:
                raise FileNotFoundError("Archivo de evidencia no disponible")
            content = resolved_path.read_bytes()
            image_analysis = await analyze_image_file(
                evidence.nombre_archivo or resolved_path.name,
                evidence.mime_type,
                f"{solicitud.descripcion} {solicitud.tipo_incidente.nombre if solicitud.tipo_incidente else ''}",
                file_bytes=content,
            )
            evidence.contenido_texto = json.dumps(
                {
                    "status": "OK",
                    "labels": image_analysis.labels,
                    "summary": image_analysis.summary,
                    "confidence": image_analysis.confidence,
                    "provider": image_analysis.provider,
                    "components": image_analysis.components,
                    "damage_zones": image_analysis.damage_zones,
                    "severity": image_analysis.severity,
                    "visual_factor": image_analysis.visual_factor,
                },
                ensure_ascii=False,
            )
            solicitud.etiquetas_ia = _merge_ai_tags(solicitud.etiquetas_ia, image_analysis.labels)
            if image_analysis.visual_factor > best_visual_factor:
                best_visual_factor = image_analysis.visual_factor
                best_summary = image_analysis.summary
            if "choque" in image_analysis.labels or "motor" in image_analysis.labels:
                solicitud.nivel_riesgo = max(solicitud.nivel_riesgo, 4)
            # Helper centralizado: proveedor, confianza, revision_manual,
            # costo_estimado_* y audit log. Idempotente.
            persist_image_ai_outcome(solicitud, image_analysis, db)
            processed += 1
        except Exception as exc:
            evidence.contenido_texto = json.dumps({"status": "ERROR", "error": str(exc)[:500]}, ensure_ascii=False)
            failed += 1

    if processed == 0:
        raise HTTPException(status_code=400, detail="No se pudo reprocesar ninguna imagen")
    if best_summary:
        solicitud.resumen_ia = best_summary
    if failed > 0:
        solicitud.requiere_revision_manual = True

    _apply_cost_estimate(solicitud)
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
        deep_link=f"/solicitudes/{solicitud.id}",
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
            deep_link=f"/solicitudes/{solicitud.id}",
        )
    await db.commit()
    await db.refresh(disputa)
    return disputa


@router.put("/{solicitud_id:int}/estado", response_model=SolicitudResponse)
async def update_request_status(
    solicitud_id: int,
    payload: SolicitudEstadoUpdate,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    current_taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    requested_state = await db.get(EstadoSolicitud, payload.estado_id)
    requested_state_name = (payload.estado_nombre or "").strip() or (requested_state.nombre if requested_state else None)

    async with _open_solicitud_session(
        db,
        solicitud_id,
        current_user,
        current_cliente_id,
        current_tecnico_id,
        current_taller_id,
    ) as (solicitud, tenant_db, actor_user_id, _cliente_id, tecnico_id, taller_id):
        if not solicitud:
            raise HTTPException(status_code=404, detail="Solicitud o estado no encontrado")

        nuevo_estado = (
            await _get_estado_por_nombre(tenant_db, requested_state_name)
            if requested_state_name
            else await tenant_db.get(EstadoSolicitud, payload.estado_id)
        )
        if not nuevo_estado:
            raise HTTPException(status_code=404, detail="Solicitud o estado no encontrado")

        roles = get_role_names(current_user)
        if not roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
            # El dueño del recurso puede operar: el técnico asignado, o el taller
            # asignado (flujo "taller sin técnico" — el taller despacha y atiende).
            es_tecnico_duenio = "TECNICO" in roles and solicitud.tecnico_id == tecnico_id
            es_taller_duenio = "TALLER" in roles and solicitud.taller_id == taller_id
            if not (es_tecnico_duenio or es_taller_duenio):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes actualizar esta solicitud")

        estado_actual = await tenant_db.get(EstadoSolicitud, solicitud.estado_id)
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
                tecnico = await tenant_db.get(Tecnico, solicitud.tecnico_id)
                if tecnico:
                    tecnico.disponibilidad = True

        cliente = await tenant_db.get(Cliente, solicitud.cliente_id)
        usuario_id = cliente.user_id if cliente else None
        taller_asignado = await tenant_db.get(Taller, solicitud.taller_id) if solicitud.taller_id else None
        tecnico_asignado = await tenant_db.get(Tecnico, solicitud.tecnico_id) if solicitud.tecnico_id else None

        estado_nombre = nuevo_estado.nombre
        _MENSAJES_CLIENTE = {
            "EN_CAMINO": ("Tu asistencia va en camino", "El equipo del taller va en camino a tu ubicación."),
            "EN_ATENCION": ("¡El equipo llegó!", "El equipo del taller llegó al lugar del incidente."),
            "COMPLETADA": ("Servicio completado", "Tu solicitud fue marcada como completada."),
            "CANCELADA": ("Solicitud cancelada", "Tu solicitud fue cancelada."),
        }
        titulo_cliente, cuerpo_cliente = _MENSAJES_CLIENTE.get(
            estado_nombre,
            ("Actualización de solicitud", f"Tu solicitud cambió a {estado_nombre}."),
        )
        mensaje_cliente = f"{cuerpo_cliente} (Solicitud #{solicitud.id})"
        notify_operational_ids: list[int] = []
        titulo_operativo: str | None = None
        mensaje_operativo: str | None = None
        tipo_operativo: str | None = None
        if estado_nombre in {"EN_CAMINO", "EN_ATENCION"}:
            notify_operational_ids.extend(await _get_operador_user_ids(tenant_db))
            if taller_asignado and taller_asignado.user_id:
                notify_operational_ids.append(taller_asignado.user_id)
            elif tecnico_asignado:
                notify_operational_ids.append(tecnico_asignado.user_id)
            if estado_nombre == "EN_CAMINO":
                titulo_operativo = "Técnico en camino"
                mensaje_operativo = f"La solicitud #{solicitud.id} salió hacia el incidente."
                tipo_operativo = "TECNICO_EN_CAMINO"
            else:
                titulo_operativo = "Equipo llegó al incidente"
                mensaje_operativo = f"La solicitud #{solicitud.id} llegó al lugar del incidente y está en atención."
                tipo_operativo = "CAMBIO_ESTADO"

        tenant_db.add(
            HistorialEvento(
                solicitud_id=solicitud.id,
                estado_anterior=estado_actual.nombre if estado_actual else "SIN_ESTADO",
                estado_nuevo=nuevo_estado.nombre,
                observacion=payload.observacion,
                usuario_id=actor_user_id,
            )
        )
        if usuario_id:
            tenant_db.add(
                Notificacion(
                    usuario_id=usuario_id,
                    titulo=titulo_cliente,
                    mensaje=mensaje_cliente,
                    tipo="CAMBIO_ESTADO",
                )
            )

        await tenant_db.commit()

        if usuario_id and estado_nombre in {"EN_CAMINO", "EN_ATENCION", "COMPLETADA", "CANCELADA"}:
            try:
                await _dispatch_push_notifications(
                    tenant_db,
                    [usuario_id],
                    titulo_cliente,
                    mensaje_cliente,
                    "CAMBIO_ESTADO",
                    deep_link=f"/solicitudes/{solicitud_id}",
                )
            except Exception:
                pass

        if notify_operational_ids and titulo_operativo and mensaje_operativo and tipo_operativo:
            try:
                await _notify_users(
                    tenant_db,
                    notify_operational_ids,
                    titulo_operativo,
                    mensaje_operativo,
                    tipo_operativo,
                    deep_link=f"/solicitudes/{solicitud_id}",
                )
                await tenant_db.commit()
            except Exception:
                await tenant_db.rollback()

        tenant: str = tenant_db.info.get("tenant_key", "default")
        await _broadcast_state_change(
            tenant,
            solicitud.id,
            nuevo_estado.nombre,
            taller_id=solicitud.taller_id,
            tecnico_id=solicitud.tecnico_id,
        )

        result = await _load_request_with_relations(tenant_db, solicitud.id)
        if not result:
            raise HTTPException(status_code=404, detail="Solicitud no encontrada")
        return result
