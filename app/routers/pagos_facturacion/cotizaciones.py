from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_user, get_current_cliente_id, get_current_tecnico_id, get_current_taller_id, get_role_names
from app.models.clientes import Cliente
from app.models.cotizaciones import Cotizacion
from app.models.solicitudes import Solicitud
from app.models.talleres import Taller
from app.models.vehiculos import Vehiculo
from app.schemas.pagos_facturacion.cotizaciones import (
    CotizacionCreateOrUpdateRequest,
    CotizacionEstadoUpdateRequest,
    CotizacionResponse,
)
from app.services.gestion_operativa_web.taller_presupuesto_service import descuento_por_marca_asociada


router = APIRouter(prefix="/cotizaciones", tags=["Cotizaciones"])

ESTADOS_VALIDOS = {"BORRADOR", "ENVIADA", "ACEPTADA", "RECHAZADA"}


def _calcular_total(items: list[dict]) -> float:
    total = 0.0
    for item in items:
        cantidad = item.get("cantidad")
        precio = item.get("precio_unitario")
        if isinstance(cantidad, (int, float)) and isinstance(precio, (int, float)):
            total += float(cantidad) * float(precio)
    return round(total, 2)


@router.get("", response_model=list[CotizacionResponse])
async def list_cotizaciones(
    current_user=Depends(get_current_user),
    cliente_id: int | None = Depends(get_current_cliente_id),
    tecnico_id: int | None = Depends(get_current_tecnico_id),
    taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> list[Cotizacion]:
    roles = get_role_names(current_user)
    query = select(Cotizacion).options(selectinload(Cotizacion.solicitud)).order_by(Cotizacion.updated_at.desc())

    if "CLIENTE" in roles and cliente_id is not None:
        query = query.join(Solicitud, Cotizacion.solicitud_id == Solicitud.id).where(Solicitud.cliente_id == cliente_id)
    elif "TECNICO" in roles and tecnico_id is not None:
        query = query.where(Cotizacion.tecnico_id == tecnico_id)
    elif "TALLER" in roles:
        if taller_id is None:
            taller_id = await db.scalar(select(Taller.id).where(Taller.user_id == current_user.id))
        if taller_id is not None:
            query = query.where(Cotizacion.taller_id == taller_id)

    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{cotizacion_id}", response_model=CotizacionResponse)
async def get_cotizacion(
    cotizacion_id: int,
    current_user=Depends(get_current_user),
    cliente_id: int | None = Depends(get_current_cliente_id),
    tecnico_id: int | None = Depends(get_current_tecnico_id),
    taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Cotizacion:
    cotizacion = await db.get(Cotizacion, cotizacion_id)
    if not cotizacion:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")

    roles = get_role_names(current_user)
    if "CLIENTE" in roles and cliente_id is not None:
        solicitud = await db.get(Solicitud, cotizacion.solicitud_id)
        if not solicitud or solicitud.cliente_id != cliente_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes ver esta cotización")
    if "TECNICO" in roles and tecnico_id is not None and cotizacion.tecnico_id != tecnico_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes ver esta cotización")
    if "TALLER" in roles and taller_id is not None and cotizacion.taller_id != taller_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes ver esta cotización")

    return cotizacion


@router.put("/solicitudes/{solicitud_id}", response_model=CotizacionResponse)
async def upsert_cotizacion_for_solicitud(
    solicitud_id: int,
    payload: CotizacionCreateOrUpdateRequest,
    current_user=Depends(get_current_user),
    tecnico_id: int | None = Depends(get_current_tecnico_id),
    taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Cotizacion:
    roles = get_role_names(current_user)
    if not roles.intersection({"ADMINISTRADOR", "OPERADOR", "TALLER", "TECNICO"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes permisos para cotizar")

    solicitud = await db.get(Solicitud, solicitud_id)
    if not solicitud:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")

    cotizacion = await db.scalar(select(Cotizacion).where(Cotizacion.solicitud_id == solicitud_id))
    if not cotizacion:
        cotizacion = Cotizacion(solicitud_id=solicitud_id)
        db.add(cotizacion)

    cotizacion.items = [item.model_dump() for item in payload.items]
    cotizacion.total = _calcular_total(cotizacion.items)
    cotizacion.moneda = payload.moneda.upper()
    if "TECNICO" in roles and tecnico_id is not None:
        cotizacion.tecnico_id = tecnico_id
    if solicitud.taller_id is not None:
        cotizacion.taller_id = solicitud.taller_id
    elif "TALLER" in roles:
        if taller_id is None:
            taller_id = await db.scalar(select(Taller.id).where(Taller.user_id == current_user.id))
        cotizacion.taller_id = taller_id

    # ── Brand-match discount ───────────────────────────────────────────────────
    # Load the vehicle's brand and the taller's associated brand to determine
    # whether the fixed 15% discount applies.
    vehiculo = await db.get(Vehiculo, solicitud.vehiculo_id)
    taller_obj = await db.get(Taller, cotizacion.taller_id) if cotizacion.taller_id else None
    descuento_pct = descuento_por_marca_asociada(
        taller_obj.marca_asociada if taller_obj else None,
        vehiculo.marca if vehiculo else None,
    )
    cotizacion.descuento_marca_pct = descuento_pct
    if descuento_pct is not None:
        cotizacion.total_final = round(cotizacion.total * (1.0 - descuento_pct / 100.0), 2)
    else:
        cotizacion.total_final = cotizacion.total

    await db.commit()
    await db.refresh(cotizacion)
    return cotizacion


@router.put("/{cotizacion_id}/estado", response_model=CotizacionResponse)
async def update_cotizacion_estado(
    cotizacion_id: int,
    payload: CotizacionEstadoUpdateRequest,
    current_user=Depends(get_current_user),
    cliente_id: int | None = Depends(get_current_cliente_id),
    taller_id: int | None = Depends(get_current_taller_id),
    db: AsyncSession = Depends(get_db),
) -> Cotizacion:
    cotizacion = await db.get(Cotizacion, cotizacion_id)
    if not cotizacion:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")

    estado = payload.estado.strip().upper()
    if estado not in ESTADOS_VALIDOS:
        raise HTTPException(status_code=400, detail="Estado no válido")

    roles = get_role_names(current_user)
    if "CLIENTE" in roles:
        if cliente_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")
        solicitud = await db.get(Solicitud, cotizacion.solicitud_id)
        if not solicitud or solicitud.cliente_id != cliente_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes modificar esta cotización")
        if estado not in {"ACEPTADA", "RECHAZADA"}:
            raise HTTPException(status_code=400, detail="El cliente solo puede aceptar o rechazar")
    elif "TALLER" in roles:
        if taller_id is not None and cotizacion.taller_id is not None and cotizacion.taller_id != taller_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No puedes modificar esta cotización")
    elif not roles.intersection({"ADMINISTRADOR", "OPERADOR"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado")

    cotizacion.estado = estado
    await db.commit()
    await db.refresh(cotizacion)
    return cotizacion

