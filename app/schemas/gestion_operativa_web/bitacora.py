"""Schemas de respuesta de la Bitácora (auditoría de acciones de usuario)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BitacoraItemResponse(BaseModel):
    id: int
    created_at: datetime
    user_id: int | None = None
    # Resuelto por JOIN a users en el endpoint — la tabla bitacora no
    # duplica el email para no desincronizarse si el usuario lo cambia.
    user_email: str | None = None
    accion: str
    metodo: str
    ruta: str
    status_code: int
    entidad: str | None = None
    entidad_id: str | None = None
    ip: str | None = None

    model_config = {"from_attributes": True}


class BitacoraListResponse(BaseModel):
    items: list[BitacoraItemResponse]
    total: int
    limit: int
    offset: int
