"""Schemas del chat en vivo cliente ↔ técnico durante una solicitud."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SolicitudChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class SolicitudChatAudioInfo(BaseModel):
    """Metadata del adjunto de audio para render + reproducción."""
    content_type: str
    duration_ms: int | None
    size_bytes: int
    # URL relativa para descargar los bytes. El cliente arma la URL
    # absoluta con environment.apiUrl / AppConfig.apiBaseUrl.
    url: str


class SolicitudChatMessageResponse(BaseModel):
    id: int
    solicitud_id: int
    sender_user_id: int
    sender_role: str  # "cliente" | "tecnico" | "taller"
    sender_display_name: str
    content: str
    created_at: datetime
    read_at: datetime | None
    # Presente solo cuando el mensaje es una nota de voz.
    audio: SolicitudChatAudioInfo | None = None

    model_config = ConfigDict(from_attributes=True)


class SolicitudChatHistoryResponse(BaseModel):
    solicitud_id: int
    messages: list[SolicitudChatMessageResponse]


class SolicitudChatReadResponse(BaseModel):
    solicitud_id: int
    marked: int
