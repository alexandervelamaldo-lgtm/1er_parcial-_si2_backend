from pydantic import EmailStr

from app.schemas.common import ORMBaseModel, TimestampedResponse
from app.schemas.roles import RoleResponse


class UserResponse(TimestampedResponse):
    id: int
    email: EmailStr
    is_active: bool
    roles: list[RoleResponse] = []
