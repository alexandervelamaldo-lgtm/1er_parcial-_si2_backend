from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import PrioridadSolicitud
from app.schemas.estados_solicitud import EstadoSolicitudResponse
from app.schemas.tipos_incidente import TipoIncidenteResponse


class SolicitudCreate(BaseModel):
    cliente_id: int
    vehiculo_id: int
    taller_id: int | None = None
    tipo_incidente_id: int
    latitud_incidente: float
    longitud_incidente: float
    descripcion: str = Field(min_length=10)
    foto_url: str | None = None
    es_carretera: bool = False
    condicion_vehiculo: str = Field(default="Operativo con limitaciones", min_length=3)
    nivel_riesgo: int = Field(default=1, ge=1, le=5)


class SolicitudAsignar(BaseModel):
    tecnico_id: int
    taller_id: int | None = None


class SolicitudEstadoUpdate(BaseModel):
    estado_id: int
    observacion: str = Field(min_length=3)


class SolicitudResponse(BaseModel):
    id: int
    cliente_id: int
    vehiculo_id: int
    tecnico_id: int | None = None
    taller_id: int | None = None
    tipo_incidente_id: int
    estado_id: int
    latitud_incidente: float
    longitud_incidente: float
    descripcion: str
    foto_url: str | None = None
    prioridad: PrioridadSolicitud
    fecha_solicitud: datetime
    fecha_asignacion: datetime | None = None
    fecha_atencion: datetime | None = None
    fecha_cierre: datetime | None = None
    estado: EstadoSolicitudResponse | None = None
    tipo_incidente: TipoIncidenteResponse | None = None

    model_config = {"from_attributes": True}
