from pydantic import BaseModel, Field

from app.schemas.users import UserResponse


class TecnicoBase(BaseModel):
    nombre: str = Field(min_length=3, max_length=150)
    telefono: str = Field(min_length=7, max_length=30)
    especialidad: str = Field(min_length=3, max_length=120)
    latitud_actual: float | None = None
    longitud_actual: float | None = None
    disponibilidad: bool = True


class TecnicoCreate(TecnicoBase):
    user_id: int


class TecnicoUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=150)
    telefono: str | None = Field(default=None, min_length=7, max_length=30)
    especialidad: str | None = Field(default=None, min_length=3, max_length=120)
    latitud_actual: float | None = None
    longitud_actual: float | None = None
    disponibilidad: bool | None = None


class UbicacionTecnicoUpdate(BaseModel):
    latitud_actual: float
    longitud_actual: float


class DisponibilidadTecnicoUpdate(BaseModel):
    disponibilidad: bool


class TecnicoResponse(TecnicoBase):
    id: int
    user_id: int
    user: UserResponse | None = None

    model_config = {"from_attributes": True}
