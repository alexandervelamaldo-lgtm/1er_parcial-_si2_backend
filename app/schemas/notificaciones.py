from datetime import datetime

from pydantic import BaseModel


class NotificacionResponse(BaseModel):
    id: int
    usuario_id: int
    titulo: str
    mensaje: str
    tipo: str
    leida: bool
    fecha_creacion: datetime

    model_config = {"from_attributes": True}
