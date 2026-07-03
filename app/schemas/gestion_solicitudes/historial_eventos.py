from datetime import datetime

from pydantic import BaseModel


class HistorialEventoResponse(BaseModel):
    id: int
    solicitud_id: int
    estado_anterior: str
    estado_nuevo: str
    observacion: str
    fecha_evento: datetime
    usuario_id: int | None = None

    model_config = {"from_attributes": True}
