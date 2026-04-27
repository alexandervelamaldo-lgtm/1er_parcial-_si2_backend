from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.estados_solicitud import EstadoSolicitud
from app.models.solicitudes import Solicitud
from app.models.tecnicos import Tecnico
from app.utils.geo import calcular_distancia_km


router = APIRouter(prefix="/mapa", tags=["Mapa"])


@router.get("/tecnicos-cercanos")
async def get_nearby_technicians(
    lat: float = Query(...),
    lon: float = Query(...),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await db.execute(select(Tecnico).where(Tecnico.disponibilidad.is_(True)))
    tecnicos = result.scalars().all()

    respuesta = []
    for tecnico in tecnicos:
        if tecnico.latitud_actual is None or tecnico.longitud_actual is None:
            continue
        distancia = calcular_distancia_km(lat, lon, tecnico.latitud_actual, tecnico.longitud_actual)
        respuesta.append(
            {
                "id": tecnico.id,
                "nombre": tecnico.nombre,
                "especialidad": tecnico.especialidad,
                "latitud_actual": tecnico.latitud_actual,
                "longitud_actual": tecnico.longitud_actual,
                "distancia_km": round(distancia, 2),
            }
        )
    return sorted(respuesta, key=lambda item: item["distancia_km"])


@router.get("/solicitudes-activas")
async def get_active_request_map(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(
        select(Solicitud, EstadoSolicitud)
        .join(EstadoSolicitud, Solicitud.estado_id == EstadoSolicitud.id)
        .where(EstadoSolicitud.nombre.not_in(["COMPLETADA", "CANCELADA"]))
    )

    return [
        {
            "id": solicitud.id,
            "latitud_incidente": solicitud.latitud_incidente,
            "longitud_incidente": solicitud.longitud_incidente,
            "prioridad": solicitud.prioridad.value,
            "estado": estado.nombre,
            "descripcion": solicitud.descripcion,
        }
        for solicitud, estado in result.all()
    ]
