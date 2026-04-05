from pydantic import BaseModel, EmailStr, Field

from app.schemas.users import UserResponse


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=64)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=64)
    rol: str
    nombre: str = Field(min_length=3, max_length=150)
    telefono: str = Field(min_length=7, max_length=30)
    direccion: str | None = Field(default=None, max_length=255)


class RegisterWorkshopRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=64)
    nombre_taller: str = Field(min_length=3, max_length=150)
    telefono: str = Field(min_length=7, max_length=30)
    direccion: str = Field(min_length=5, max_length=255)
    latitud: float
    longitud: float
    capacidad: int = Field(ge=1, le=1000)
    servicios: list[str] = Field(default_factory=list)


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    new_password: str = Field(min_length=6, max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=6, max_length=64)
    new_password: str = Field(min_length=6, max_length=64)


class CurrentUserProfileResponse(BaseModel):
    user: UserResponse
    cliente_id: int | None = None
    tecnico_id: int | None = None
    operador_id: int | None = None
    taller_id: int | None = None
