from datetime import datetime

from pydantic import BaseModel, Field


class NotificacionResponse(BaseModel):
    id: int
    usuario_id: int
    titulo: str
    mensaje: str
    tipo: str
    leida: bool
    fecha_creacion: datetime

    model_config = {"from_attributes": True}


class DeviceTokenRegisterRequest(BaseModel):
    token: str = Field(min_length=20, max_length=255)
    plataforma: str = Field(default="mobile", min_length=3, max_length=30)
