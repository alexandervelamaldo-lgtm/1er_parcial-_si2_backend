"""Schemas del chat en vivo cliente ↔ técnico durante una solicitud."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SolicitudChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class SolicitudChatMessageResponse(BaseModel):
    id: int
    solicitud_id: int
    sender_user_id: int
    sender_role: str  # "cliente" | "tecnico"
    sender_display_name: str
    content: str
    created_at: datetime
    read_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class SolicitudChatHistoryResponse(BaseModel):
    solicitud_id: int
    messages: list[SolicitudChatMessageResponse]


class SolicitudChatReadResponse(BaseModel):
    solicitud_id: int
    marked: int
