from pydantic import BaseModel, Field

from app.schemas.users import UserResponse


class ClienteBase(BaseModel):
    nombre: str = Field(min_length=3, max_length=150)
    telefono: str = Field(min_length=7, max_length=30)
    direccion: str = Field(min_length=5, max_length=255)
    latitud: float | None = None
    longitud: float | None = None


class ClienteCreate(ClienteBase):
    user_id: int


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

    model_config = {"from_attributes": True}
