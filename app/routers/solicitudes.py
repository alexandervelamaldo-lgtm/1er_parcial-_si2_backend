from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import (
    get_current_cliente_id,
    get_current_tecnico_id,
    get_current_user,
    get_role_names,
    require_roles,
)
from app.models.clientes import Cliente
from app.models.estados_solicitud import EstadoSolicitud
from app.models.historial_eventos import HistorialEvento
from app.models.notificaciones import Notificacion
from app.models.solicitudes import Solicitud
from app.models.tecnicos import Tecnico
from app.models.tipos_incidente import TipoIncidente
from app.models.users import User
from app.models.vehiculos import Vehiculo
from app.schemas.solicitudes import SolicitudAsignar, SolicitudCreate, SolicitudEstadoUpdate, SolicitudResponse
from app.services.prioridad_service import calcular_prioridad


router = APIRouter(prefix="/solicitudes", tags=["Solicitudes"])


async def _get_estado_por_nombre(db: AsyncSession, nombre: str) -> EstadoSolicitud:
    estado = await db.scalar(select(EstadoSolicitud).where(EstadoSolicitud.nombre == nombre))
    if not estado:
        raise HTTPException(status_code=404, detail=f"Estado {nombre} no encontrado")
    return estado


def validate_request_access(
    current_user: User,
    current_cliente_id: int | None,
    current_tecnico_id: int | None,
    solicitud: Solicitud,
) -> None:
    roles = get_role_names(current_user)
    if roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        return
    if "CLIENTE" in roles and current_cliente_id == solicitud.cliente_id:
        return
    if "TECNICO" in roles and current_tecnico_id == solicitud.tecnico_id:
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

    # La prioridad combina riesgo operativo y contexto del incidente.
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
        prioridad=prioridad,
    )
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
        Notificacion(
            usuario_id=cliente.user_id,
            titulo="Solicitud registrada",
            mensaje=f"Tu solicitud #{solicitud.id} fue registrada con prioridad {prioridad.value}.",
            tipo="SOLICITUD_REGISTRADA",
        )
    )
    await db.commit()

    result = await db.execute(
        select(Solicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .where(Solicitud.id == solicitud.id)
    )
    return result.scalar_one()


@router.get("", response_model=list[SolicitudResponse])
async def list_requests(
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
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
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/activas", response_model=list[SolicitudResponse])
async def list_active_requests(
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
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


@router.get("/{solicitud_id}", response_model=SolicitudResponse)
async def get_request(
    solicitud_id: int,
    current_user: User = Depends(get_current_user),
    current_cliente_id: int | None = Depends(get_current_cliente_id),
    current_tecnico_id: int | None = Depends(get_current_tecnico_id),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    result = await db.execute(
        select(Solicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .where(Solicitud.id == solicitud_id)
    )
    solicitud = result.scalar_one_or_none()
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    validate_request_access(current_user, current_cliente_id, current_tecnico_id, solicitud)
    return solicitud


@router.put("/{solicitud_id}/asignar", response_model=SolicitudResponse)
async def assign_request(
    solicitud_id: int,
    payload: SolicitudAsignar,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> Solicitud:
    solicitud = await db.get(Solicitud, solicitud_id)
    tecnico = await db.get(Tecnico, payload.tecnico_id)
    if not solicitud or not tecnico:
        raise HTTPException(status_code=404, detail="Solicitud o técnico no encontrado")

    estado_anterior = await db.get(EstadoSolicitud, solicitud.estado_id)
    estado_asignada = await _get_estado_por_nombre(db, "ASIGNADA")

    solicitud.tecnico_id = payload.tecnico_id
    solicitud.taller_id = payload.taller_id
    solicitud.estado_id = estado_asignada.id
    solicitud.fecha_asignacion = datetime.now(timezone.utc)

    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_anterior.nombre if estado_anterior else "SIN_ESTADO",
            estado_nuevo=estado_asignada.nombre,
            observacion=f"Técnico {tecnico.nombre} asignado",
            usuario_id=tecnico.user_id,
        )
    )
    db.add(
        Notificacion(
            usuario_id=tecnico.user_id,
            titulo="Nueva asignación",
            mensaje=f"Se te asignó la solicitud #{solicitud.id}.",
            tipo="ASIGNACION_TECNICO",
        )
    )
    await db.commit()

    result = await db.execute(
        select(Solicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .where(Solicitud.id == solicitud.id)
    )
    return result.scalar_one()


@router.put("/{solicitud_id}/estado", response_model=SolicitudResponse)
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
    solicitud.estado_id = nuevo_estado.id

    if nuevo_estado.nombre == "EN_ATENCION":
        solicitud.fecha_atencion = datetime.now(timezone.utc)
    if nuevo_estado.nombre in {"COMPLETADA", "CANCELADA"}:
        solicitud.fecha_cierre = datetime.now(timezone.utc)

    cliente = await db.get(Cliente, solicitud.cliente_id)
    usuario_id = cliente.user_id if cliente else None
    db.add(
        HistorialEvento(
            solicitud_id=solicitud.id,
            estado_anterior=estado_actual.nombre if estado_actual else "SIN_ESTADO",
            estado_nuevo=nuevo_estado.nombre,
            observacion=payload.observacion,
            usuario_id=usuario_id,
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

    result = await db.execute(
        select(Solicitud)
        .options(selectinload(Solicitud.estado), selectinload(Solicitud.tipo_incidente))
        .where(Solicitud.id == solicitud.id)
    )
    return result.scalar_one()
