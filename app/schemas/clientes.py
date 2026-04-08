from pydantic import BaseModel, EmailStr, Field, model_validator

from app.schemas.users import UserResponse
from app.schemas.vehiculos import VehiculoResponse


class ClienteBase(BaseModel):
    nombre: str = Field(min_length=3, max_length=150)
    telefono: str = Field(min_length=7, max_length=30)
    direccion: str = Field(min_length=5, max_length=255)
    latitud: float | None = None
    longitud: float | None = None


class ClienteVehiculoCreate(BaseModel):
    marca: str = Field(min_length=2, max_length=100)
    modelo: str = Field(min_length=1, max_length=100)
    anio: int = Field(ge=1950, le=2100)
    placa: str = Field(min_length=5, max_length=20)
    color: str = Field(min_length=3, max_length=50)
    tipo_combustible: str = Field(default="Gasolina", min_length=3, max_length=50)


class ClienteCreate(ClienteBase):
    user_id: int | None = None
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=6, max_length=64)
    vehiculo: ClienteVehiculoCreate

    @model_validator(mode="after")
    def validate_user_source(self) -> "ClienteCreate":
        if self.user_id is not None:
            return self
        if self.email and self.password:
            return self
        raise ValueError("Debes indicar un user_id existente o email y password para crear el acceso del cliente")


class ClienteUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=150)
    telefono: str | None = Field(default=None, min_length=7, max_length=30)
    direccion: str | None = Field(default=None, min_length=5, max_length=255)
    latitud: float | None = None
    longitud: float | None = None


class ClienteResponse(ClienteBase):
    id: int
    user_id: int
    user: UserResponse | None = None
    vehiculos: list[VehiculoResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}
