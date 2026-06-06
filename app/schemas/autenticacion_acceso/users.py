from pydantic import EmailStr

from app.schemas.autenticacion_acceso.common import ORMBaseModel, TimestampedResponse
from app.schemas.autenticacion_acceso.roles import RoleResponse


class UserResponse(TimestampedResponse):
    id: int
    email: EmailStr
    is_active: bool
    roles: list[RoleResponse] = []
