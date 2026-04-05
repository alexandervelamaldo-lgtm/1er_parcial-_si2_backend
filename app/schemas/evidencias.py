from datetime import datetime

from pydantic import BaseModel, Field


class EvidenciaTextoCreate(BaseModel):
    contenido_texto: str = Field(min_length=3, max_length=5000)


class EvidenciaResponse(BaseModel):
    id: int
    solicitud_id: int
    usuario_id: int | None = None
    tipo: str
    nombre_archivo: str | None = None
    contenido_texto: str | None = None
    archivo_url: str | None = None
    mime_type: str | None = None
    tamano_bytes: int | None = None
    fecha_creacion: datetime

    model_config = {"from_attributes": True}
