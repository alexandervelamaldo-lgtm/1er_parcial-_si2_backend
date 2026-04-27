from pydantic import BaseModel


class EstadoSolicitudResponse(BaseModel):
    id: int
    nombre: str

    model_config = {"from_attributes": True}
