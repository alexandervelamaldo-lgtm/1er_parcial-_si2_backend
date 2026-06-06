"""Schemas de la API de respaldos por tenant.

Las respuestas reflejan los dicts que produce
``app.services.gestion_operativa_web.backup_service`` (``_meta`` y
``load_schedule``). La validación de entrada del schedule duplica — a
propósito — los límites del servicio para devolver un 422 claro antes de
tocar el disco.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class BackupItem(BaseModel):
    """Un archivo de respaldo (.dump) del tenant."""

    name: str
    size_bytes: int
    size_human: str
    created_at: datetime
    # "manual" → creado por el usuario; "auto" → creado por el scheduler.
    kind: Literal["manual", "auto"]

    model_config = {"from_attributes": True}


class BackupListResponse(BaseModel):
    items: list[BackupItem]
    total: int
    # Si pg_dump/pg_restore no están en el servidor, la UI deshabilita las
    # acciones y muestra un aviso en vez de fallar al primer click.
    pg_available: bool


class ScheduleConfig(BaseModel):
    """Cuerpo de PUT /backups/schedule (configuración del backup automático)."""

    enabled: bool = False
    frequency: Literal["hourly", "daily", "weekly"] = "daily"
    # Solo aplica a la frecuencia diaria: hora local del servidor (0-23).
    hour: int = Field(default=2, ge=0, le=23)
    # Cuántos respaldos AUTOMÁTICOS conservar antes de podar los más viejos.
    retention: int = Field(default=7, ge=1, le=50)


class ScheduleResponse(ScheduleConfig):
    # ISO 8601. last_run lo escribe el scheduler; next_run es estimado.
    last_run: str | None = None
    next_run: str | None = None


class MessageResponse(BaseModel):
    detail: str
