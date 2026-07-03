from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_taller_id, get_current_user, require_roles
from app.models.pagos import PagoSolicitud
from app.models.roles import Role
from app.models.taller_categorias import CategoriaTaller
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.schemas.pagos_facturacion.pagos import TallerFinanzasResumenResponse
from app.schemas.gestion_operativa_web.categorias_taller import CategoriaTallerResponse
from app.schemas.gestion_operativa_web.talleres import TallerAdminCreate, TallerMapaResponse, TallerResponse, TallerUpdate
from app.schemas.gestion_operativa_web.tecnicos import TecnicoResponse, TecnicoWorkshopCreate
from app.utils.auth import hash_password
from app.utils.geo import calcular_distancia_km
from app.services.gestion_operativa_web.taller_presupuesto_service import calcular_presupuesto_estimado
from app.services.gestion_operativa_web.taller_filtro_service import categorias_permitidas_por_problema
from app.services.gestion_operativa_web.taller_identidad_service import same_workshop_identity
from app.services.inteligencia_automatizacion.prioridad_service import motivo_categoria_dano


router = APIRouter(prefix="/talleres", tags=["Talleres"])


async def _ensure_unique_workshop(
    db: AsyncSession,
    *,
    nombre: str,
    telefono: str,
    exclude_id: int | None = None,
) -> None:
    result = await db.execute(select(Taller))
    for existing in result.scalars().all():
        if exclude_id is not None and existing.id == exclude_id:
            continue
        if same_workshop_identity(
            left_name=existing.nombre,
            left_phone=existing.telefono,
            right_name=nombre,
            right_phone=telefono,
        ):
            raise HTTPException(
                status_code=400,
                detail="Ya existe un taller registrado con el mismo nombre y teléfono",
            )


def serialize_workshop(taller: Taller, distancia_km: float | None = None) -> TallerResponse:
    categoria = CategoriaTallerResponse.model_validate(taller.categoria) if getattr(taller, "categoria", None) else None
    return TallerResponse(
        id=taller.id,
        nombre=taller.nombre,
        direccion=taller.direccion,
        latitud=taller.latitud,
        longitud=taller.longitud,
        telefono=taller.telefono,
        horarios=taller.horarios,
        certificaciones=taller.certificaciones,
        tarifas_base=taller.tarifas_base,
        descuentos_marca=taller.descuentos_marca,
        rating_promedio=taller.rating_promedio,
        rating_total=taller.rating_total,
        capacidad=taller.capacidad,
        servicios=taller.servicios.split("|") if taller.servicios else [],
        disponible=taller.disponible,
        acepta_automaticamente=taller.acepta_automaticamente,
        user_id=taller.user_id,
        distancia_km=distancia_km,
        categoria=categoria,
    )


@router.get("", response_model=list[TallerResponse])
async def list_workshops(db: AsyncSession = Depends(get_db)) -> list[TallerResponse]:
    result = await db.execute(select(Taller).options(selectinload(Taller.categoria)).where(Taller.disponible.is_(True)))
    return [serialize_workshop(taller) for taller in result.scalars().all()]


@router.post("", response_model=TallerResponse, status_code=status.HTTP_201_CREATED)
async def create_workshop(
    payload: TallerAdminCreate,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> TallerResponse:
    categoria = await db.get(CategoriaTaller, payload.categoria_id)
    if not categoria:
        raise HTTPException(status_code=400, detail="Categoría de taller inválida")
    await _ensure_unique_workshop(db, nombre=payload.nombre, telefono=payload.telefono)

    user_id: int | None = None
    if payload.email and payload.password:
        existing_user = await db.scalar(select(User).where(User.email == payload.email))
        if existing_user:
            raise HTTPException(status_code=400, detail="El correo ya está registrado")
        taller_role = await db.scalar(select(Role).where(Role.name == "TALLER"))
        if not taller_role:
            raise HTTPException(status_code=400, detail="Rol taller no configurado")
        user = User(email=payload.email, password_hash=hash_password(payload.password))
        user.roles.append(taller_role)
        db.add(user)
        await db.flush()
        user_id = user.id

    servicios = "|".join(payload.servicios) if payload.servicios else ""
    taller = Taller(
        user_id=user_id,
        categoria_id=payload.categoria_id,
        nombre=payload.nombre,
        direccion=payload.direccion,
        latitud=payload.latitud,
        longitud=payload.longitud,
        telefono=payload.telefono,
        horarios=payload.horarios,
        certificaciones=payload.certificaciones,
        tarifas_base=payload.tarifas_base,
        descuentos_marca=payload.descuentos_marca,
        rating_promedio=payload.rating_promedio,
        rating_total=payload.rating_total,
        capacidad=payload.capacidad,
        servicios=servicios,
        disponible=payload.disponible,
        acepta_automaticamente=payload.acepta_automaticamente,
    )
    db.add(taller)
    await db.commit()
    taller = await db.get(Taller, taller.id)
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    return serialize_workshop(taller)


@router.put("/{taller_id:int}", response_model=TallerResponse)
async def update_workshop(
    taller_id: int,
    payload: TallerUpdate,
    _: User = Depends(require_roles("ADMINISTRADOR", "OPERADOR")),
    db: AsyncSession = Depends(get_db),
) -> TallerResponse:
    taller = await db.get(Taller, taller_id)
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    update_data = payload.model_dump(exclude_unset=True)
    if "servicios" in update_data and update_data["servicios"] is not None:
        update_data["servicios"] = "|".join(update_data["servicios"])
    if "categoria_id" in update_data and update_data["categoria_id"] is not None:
        categoria = await db.get(CategoriaTaller, update_data["categoria_id"])
        if not categoria:
            raise HTTPException(status_code=400, detail="Categoría de taller inválida")
    if "nombre" in update_data or "telefono" in update_data:
        await _ensure_unique_workshop(
            db,
            nombre=str(update_data.get("nombre", taller.nombre)),
            telefono=str(update_data.get("telefono", taller.telefono)),
            exclude_id=taller.id,
        )
    for field, value in update_data.items():
        setattr(taller, field, value)
    await db.commit()
    await db.refresh(taller)
    return serialize_workshop(taller)


@router.get("/categorias", response_model=list[CategoriaTallerResponse])
async def list_workshop_categories(db: AsyncSession = Depends(get_db)) -> list[CategoriaTallerResponse]:
    result = await db.execute(select(CategoriaTaller).order_by(CategoriaTaller.nombre.asc()))
    return [CategoriaTallerResponse.model_validate(c) for c in result.scalars().all()]


@router.get("/mapa", response_model=list[TallerMapaResponse])
async def list_workshops_for_map(
    categoria_id: int | None = Query(default=None),
    dano_categoria: str | None = Query(default=None),
    marca: str | None = Query(default=None),
    lat: float | None = Query(default=None),
    lon: float | None = Query(default=None),
    radio: float = Query(default=25.0, gt=0),
    db: AsyncSession = Depends(get_db),
) -> list[TallerMapaResponse]:
    stmt = select(Taller).join(CategoriaTaller).options(selectinload(Taller.categoria)).where(Taller.disponible.is_(True))
    slugs: set[str] = set()
    if categoria_id is not None:
        stmt = stmt.where(Taller.categoria_id == categoria_id)
    elif dano_categoria:
        slugs = categorias_permitidas_por_problema(dano_categoria)
        stmt = stmt.where(CategoriaTaller.slug.in_(sorted(slugs)))
    result = await db.execute(stmt)
    talleres = result.scalars().all()

    # Filtrado estricto por tipo de servicio: una gomería sólo ve talleres de
    # 'llantas', un choque sólo 'chaperia_pintura', etc. Si la categoría
    # especializada no tiene talleres disponibles caemos a 'general'
    # (multiservicio) para no devolver un mapa vacío. Sólo aplica al filtrar por
    # daño (no cuando el cliente fijó una categoría explícita) y cuando la
    # categoría pedida no era ya 'general'.
    fallback_general = False
    if dano_categoria and categoria_id is None and not talleres and "general" not in slugs:
        fallback_stmt = (
            select(Taller)
            .join(CategoriaTaller)
            .options(selectinload(Taller.categoria))
            .where(Taller.disponible.is_(True), CategoriaTaller.slug == "general")
        )
        talleres = (await db.execute(fallback_stmt)).scalars().all()
        fallback_general = bool(talleres)

    motivo = motivo_categoria_dano(dano_categoria)
    if fallback_general:
        motivo = (
            "No hay talleres especializados disponibles para este servicio; "
            "se muestran talleres generales (multiservicio)."
        )
    items: list[TallerMapaResponse] = []
    for taller in talleres:
        distancia_km: float | None = None
        if lat is not None and lon is not None:
            distancia_km = round(calcular_distancia_km(lat, lon, taller.latitud, taller.longitud), 2)
            if distancia_km > radio:
                continue
        base = serialize_workshop(taller, distancia_km=distancia_km)
        presupuesto = calcular_presupuesto_estimado(
            dano_categoria=dano_categoria,
            tarifas_base=taller.tarifas_base,
            descuentos_marca=taller.descuentos_marca,
            marca_vehiculo=marca,
        )
        items.append(
            TallerMapaResponse(
                **base.model_dump(),
                presupuesto_min=presupuesto.presupuesto_min,
                presupuesto_max=presupuesto.presupuesto_max,
                presupuesto_descuento_min=presupuesto.presupuesto_descuento_min,
                presupuesto_descuento_max=presupuesto.presupuesto_descuento_max,
                descuento_porcentaje_aplicado=presupuesto.descuento_porcentaje_aplicado,
                tiempo_reparacion_horas=presupuesto.tiempo_reparacion_horas,
                motivo_sugerencia=motivo,
            )
        )
    return sorted(items, key=lambda it: (it.distancia_km is None, it.distancia_km or 0.0))

@router.get("/cercanos", response_model=list[TallerResponse])
async def list_nearby_workshops(
    lat: float = Query(...),
    lon: float = Query(...),
    radio: float = Query(default=10.0, gt=0),
    db: AsyncSession = Depends(get_db),
) -> list[TallerResponse]:
    result = await db.execute(select(Taller).where(Taller.disponible.is_(True)))
    talleres = result.scalars().all()

    encontrados: list[TallerResponse] = []
    for taller in talleres:
        distancia = calcular_distancia_km(lat, lon, taller.latitud, taller.longitud)
        if distancia <= radio:
            encontrados.append(serialize_workshop(taller, round(distancia, 2)))
    return sorted(encontrados, key=lambda item: item.distancia_km or 0)


@router.get("/mi-taller", response_model=TallerResponse)
async def get_my_workshop(
    current_taller_id: int | None = Depends(get_current_taller_id),
    _: User = Depends(require_roles("TALLER")),
    db: AsyncSession = Depends(get_db),
) -> TallerResponse:
    if current_taller_id is None:
        raise HTTPException(status_code=404, detail="No se encontró el taller autenticado")
    taller = await db.get(Taller, current_taller_id)
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    return serialize_workshop(taller)


@router.put("/mi-taller", response_model=TallerResponse)
async def update_my_workshop(
    payload: TallerUpdate,
    current_taller_id: int | None = Depends(get_current_taller_id),
    _: User = Depends(require_roles("TALLER")),
    db: AsyncSession = Depends(get_db),
) -> TallerResponse:
    if current_taller_id is None:
        raise HTTPException(status_code=404, detail="No se encontró el taller autenticado")
    taller = await db.get(Taller, current_taller_id)
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    update_data = payload.model_dump(exclude_unset=True)
    if "servicios" in update_data and update_data["servicios"] is not None:
        update_data["servicios"] = "|".join(update_data["servicios"])
    if "categoria_id" in update_data and update_data["categoria_id"] is not None:
        categoria = await db.get(CategoriaTaller, update_data["categoria_id"])
        if not categoria:
            raise HTTPException(status_code=400, detail="Categoría de taller inválida")
    if "nombre" in update_data or "telefono" in update_data:
        await _ensure_unique_workshop(
            db,
            nombre=str(update_data.get("nombre", taller.nombre)),
            telefono=str(update_data.get("telefono", taller.telefono)),
            exclude_id=taller.id,
        )
    for field, value in update_data.items():
        setattr(taller, field, value)
    await db.commit()
    await db.refresh(taller)
    return serialize_workshop(taller)


@router.get("/mi-taller/tecnicos", response_model=list[TecnicoResponse])
async def list_my_workshop_technicians(
    current_taller_id: int | None = Depends(get_current_taller_id),
    _: User = Depends(require_roles("TALLER")),
    db: AsyncSession = Depends(get_db),
) -> list[Tecnico]:
    if current_taller_id is None:
        raise HTTPException(status_code=404, detail="No se encontró el taller autenticado")
    result = await db.execute(
        select(Tecnico).options(selectinload(Tecnico.user)).where(Tecnico.taller_id == current_taller_id)
    )
    return list(result.scalars().all())


@router.post("/mi-taller/tecnicos", response_model=TecnicoResponse, status_code=status.HTTP_201_CREATED)
async def create_my_workshop_technician(
    payload: TecnicoWorkshopCreate,
    current_taller_id: int | None = Depends(get_current_taller_id),
    _: User = Depends(require_roles("TALLER")),
    db: AsyncSession = Depends(get_db),
) -> Tecnico:
    if current_taller_id is None:
        raise HTTPException(status_code=404, detail="No se encontró el taller autenticado")
    existing_user = await db.scalar(select(User).where(User.email == payload.email))
    if existing_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    tecnico_role = await db.scalar(select(Role).where(Role.name == "TECNICO"))
    if not tecnico_role:
        raise HTTPException(status_code=400, detail="Rol técnico no configurado")
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    user.roles.append(tecnico_role)
    db.add(user)
    await db.flush()
    tecnico = Tecnico(
        user_id=user.id,
        taller_id=current_taller_id,
        nombre=payload.nombre,
        telefono=payload.telefono,
        especialidad=payload.especialidad,
        disponibilidad=True,
    )
    db.add(tecnico)
    await db.commit()
    result = await db.execute(select(Tecnico).options(selectinload(Tecnico.user)).where(Tecnico.id == tecnico.id))
    return result.scalar_one()


@router.get("/mi-taller/finanzas", response_model=TallerFinanzasResumenResponse)
async def get_my_workshop_finances(
    current_taller_id: int | None = Depends(get_current_taller_id),
    _: User = Depends(require_roles("TALLER")),
    db: AsyncSession = Depends(get_db),
) -> TallerFinanzasResumenResponse:
    if current_taller_id is None:
        raise HTTPException(status_code=404, detail="No se encontró el taller autenticado")
    result = await db.execute(
        select(
            func.count(PagoSolicitud.id),
            func.coalesce(func.sum(PagoSolicitud.monto_total), 0.0),
            func.coalesce(func.sum(PagoSolicitud.monto_comision), 0.0),
            func.coalesce(func.sum(PagoSolicitud.monto_taller), 0.0),
        ).where(PagoSolicitud.taller_id == current_taller_id, PagoSolicitud.estado == "PAGADO")
    )
    total_pagos, total_facturado, total_comision, total_taller = result.one()
    return TallerFinanzasResumenResponse(
        taller_id=current_taller_id,
        total_pagos=total_pagos or 0,
        total_facturado=float(total_facturado or 0),
        total_comision=float(total_comision or 0),
        total_taller=float(total_taller or 0),
    )
