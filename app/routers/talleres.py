from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_taller_id, get_current_user, require_roles
from app.models.pagos import PagoSolicitud
from app.models.roles import Role
from app.models.talleres import Taller
from app.models.tecnicos import Tecnico
from app.models.users import User
from app.schemas.pagos import TallerFinanzasResumenResponse
from app.schemas.talleres import TallerResponse, TallerUpdate
from app.schemas.tecnicos import TecnicoResponse, TecnicoWorkshopCreate
from app.utils.auth import hash_password
from app.utils.geo import calcular_distancia_km


router = APIRouter(prefix="/talleres", tags=["Talleres"])


@router.get("", response_model=list[TallerResponse])
async def list_workshops(db: AsyncSession = Depends(get_db)) -> list[Taller]:
    result = await db.execute(select(Taller).where(Taller.disponible.is_(True)))
    return list(result.scalars().all())


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
            encontrados.append(
                TallerResponse(
                    id=taller.id,
                    nombre=taller.nombre,
                    direccion=taller.direccion,
                    latitud=taller.latitud,
                    longitud=taller.longitud,
                    telefono=taller.telefono,
                    capacidad=taller.capacidad,
                    servicios=taller.servicios.split("|") if taller.servicios else [],
                    disponible=taller.disponible,
                    acepta_automaticamente=taller.acepta_automaticamente,
                    user_id=taller.user_id,
                    distancia_km=round(distancia, 2),
                )
            )
    return sorted(encontrados, key=lambda item: item.distancia_km or 0)


@router.get("/mi-taller", response_model=TallerResponse)
async def get_my_workshop(
    current_taller_id: int | None = Depends(get_current_taller_id),
    _: User = Depends(require_roles("TALLER")),
    db: AsyncSession = Depends(get_db),
) -> Taller:
    if current_taller_id is None:
        raise HTTPException(status_code=404, detail="No se encontró el taller autenticado")
    taller = await db.get(Taller, current_taller_id)
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    return taller


@router.put("/mi-taller", response_model=TallerResponse)
async def update_my_workshop(
    payload: TallerUpdate,
    current_taller_id: int | None = Depends(get_current_taller_id),
    _: User = Depends(require_roles("TALLER")),
    db: AsyncSession = Depends(get_db),
) -> Taller:
    if current_taller_id is None:
        raise HTTPException(status_code=404, detail="No se encontró el taller autenticado")
    taller = await db.get(Taller, current_taller_id)
    if not taller:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    update_data = payload.model_dump(exclude_unset=True)
    if "servicios" in update_data and update_data["servicios"] is not None:
        update_data["servicios"] = "|".join(update_data["servicios"])
    for field, value in update_data.items():
        setattr(taller, field, value)
    await db.commit()
    await db.refresh(taller)
    return taller


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
