from pydantic import BaseModel, Field


class TallerBase(BaseModel):
    nombre: str = Field(min_length=3, max_length=150)
    direccion: str = Field(min_length=5, max_length=255)
    latitud: float
    longitud: float
    telefono: str = Field(min_length=7, max_length=30)
    capacidad: int = Field(ge=1, le=1000)
    servicios: list[str] = Field(default_factory=list)
    disponible: bool = True
    acepta_automaticamente: bool = False


class TallerUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=150)
    direccion: str | None = Field(default=None, min_length=5, max_length=255)
    latitud: float | None = None
    longitud: float | None = None
    telefono: str | None = Field(default=None, min_length=7, max_length=30)
    capacidad: int | None = Field(default=None, ge=1, le=1000)
    servicios: list[str] | None = None
    disponible: bool | None = None
    acepta_automaticamente: bool | None = None


class TallerResponse(TallerBase):
    id: int
    user_id: int | None = None
    distancia_km: float | None = None
    score: float | None = None
    match_especializacion: bool = False
    motivo_sugerencia: str | None = None

    model_config = {"from_attributes": True}
