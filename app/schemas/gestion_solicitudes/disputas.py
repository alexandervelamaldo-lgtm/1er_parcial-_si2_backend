from datetime import datetime

from pydantic import BaseModel, Field


class DisputaCreate(BaseModel):
    motivo: str = Field(min_length=3, max_length=150)
    detalle: str = Field(min_length=5, max_length=5000)


class DisputaResolverRequest(BaseModel):
    resolucion: str = Field(min_length=5, max_length=5000)


class DisputaResponse(BaseModel):
    id: int
    solicitud_id: int
    usuario_id: int
    motivo: str
    detalle: str
    estado: str
    resolucion: str | None = None
    fecha_creacion: datetime
    fecha_resolucion: datetime | None = None

    model_config = {"from_attributes": True}
