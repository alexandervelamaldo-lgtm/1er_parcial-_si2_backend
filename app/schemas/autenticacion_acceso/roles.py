from app.schemas.autenticacion_acceso.common import ORMBaseModel


class RoleResponse(ORMBaseModel):
    id: int
    name: str
