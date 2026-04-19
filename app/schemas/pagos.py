from datetime import datetime

from pydantic import BaseModel, Field


class PagoCreate(BaseModel):
    monto_total: float | None = Field(default=None, gt=0)
    metodo_pago: str = Field(min_length=3, max_length=40)
    referencia_externa: str | None = Field(default=None, max_length=120)
    observacion: str | None = Field(default=None, max_length=500)
    confirmar_pago: bool = True


class PagoResponse(BaseModel):
    id: int
    solicitud_id: int
    cliente_id: int
    taller_id: int | None = None
    monto_total: float
    monto_comision: float
    monto_taller: float
    metodo_pago: str
    estado: str
    referencia_externa: str | None = None
    observacion: str | None = None
    fecha_creacion: datetime
    fecha_pago: datetime | None = None

    model_config = {"from_attributes": True}


class TallerFinanzasResumenResponse(BaseModel):
    taller_id: int
    total_pagos: int
    total_facturado: float
    total_comision: float
    total_taller: float
