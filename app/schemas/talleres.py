from pydantic import BaseModel, Field


class TallerBase(BaseModel):
    nombre: str = Field(min_length=3, max_length=150)
    direccion: str = Field(min_length=5, max_length=255)
    latitud: float
    longitud: float
    telefono: str = Field(min_length=7, max_length=30)
    capacidad: int = Field(ge=1, le=1000)


class TallerResponse(TallerBase):
    id: int
    distancia_km: float | None = None

    model_config = {"from_attributes": True}
