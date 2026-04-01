from app.schemas.common import ORMBaseModel


class RoleResponse(ORMBaseModel):
    id: int
    name: str
