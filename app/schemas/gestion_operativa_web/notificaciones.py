from datetime import datetime

from pydantic import BaseModel, Field


class NotificacionResponse(BaseModel):
    id: int
    usuario_id: int
    titulo: str
    mensaje: str
    tipo: str
    diagnostico_categoria: str | None = None
    leida: bool
    fecha_creacion: datetime

    model_config = {"from_attributes": True}


class DeviceTokenRegisterRequest(BaseModel):
    token: str = Field(min_length=20, max_length=255)
    plataforma: str = Field(default="mobile", min_length=3, max_length=30)


class WebPushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=10, max_length=512)


class WebPushSubscriptionRegisterRequest(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)
    keys: WebPushKeys
    expirationTime: str | None = None
    userAgent: str | None = Field(default=None, max_length=255)


class WebPushPublicKeyResponse(BaseModel):
    publicKey: str


class NotificationPreferencesResponse(BaseModel):
    disabledAll: bool
    disabledTypes: dict[str, bool]


class NotificationPreferencesUpdateRequest(BaseModel):
    disabledAll: bool | None = None
    disabledTypes: dict[str, bool] | None = None
