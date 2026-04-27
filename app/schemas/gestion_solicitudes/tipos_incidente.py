from pydantic import BaseModel, Field


class TipoIncidenteResponse(BaseModel):
    id: int
    nombre: str = Field(max_length=120)
    descripcion: str

    model_config = {"from_attributes": True}
