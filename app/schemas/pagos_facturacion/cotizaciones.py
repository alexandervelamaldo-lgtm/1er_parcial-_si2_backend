from datetime import datetime

from pydantic import BaseModel, Field


class CotizacionItem(BaseModel):
    concepto: str = Field(min_length=2, max_length=200)
    cantidad: float = Field(gt=0)
    precio_unitario: float = Field(ge=0)


class CotizacionCreateOrUpdateRequest(BaseModel):
    items: list[CotizacionItem] = Field(default_factory=list)
    moneda: str = Field(default="BOB", min_length=2, max_length=8)


class CotizacionEstadoUpdateRequest(BaseModel):
    estado: str = Field(min_length=3, max_length=30)


class CotizacionResponse(BaseModel):
    id: int
    solicitud_id: int
    taller_id: int | None
    tecnico_id: int | None
    estado: str
    items: list[dict]
    total: float
    descuento_marca_pct: float | None = None
    total_final: float
    moneda: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

