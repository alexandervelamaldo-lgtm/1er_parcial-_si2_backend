from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.talleres import Taller
from app.schemas.talleres import TallerResponse
from app.utils.geo import calcular_distancia_km


router = APIRouter(prefix="/talleres", tags=["Talleres"])


@router.get("", response_model=list[TallerResponse])
async def list_workshops(db: AsyncSession = Depends(get_db)) -> list[Taller]:
    result = await db.execute(select(Taller))
    return list(result.scalars().all())


@router.get("/cercanos", response_model=list[TallerResponse])
async def list_nearby_workshops(
    lat: float = Query(...),
    lon: float = Query(...),
    radio: float = Query(default=10.0, gt=0),
    db: AsyncSession = Depends(get_db),
) -> list[TallerResponse]:
    result = await db.execute(select(Taller))
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
                    distancia_km=round(distancia, 2),
                )
            )
    return sorted(encontrados, key=lambda item: item.distancia_km or 0)
