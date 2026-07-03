from pydantic import BaseModel, Field


class OperadorResponse(BaseModel):
    id: int
    user_id: int
    nombre: str = Field(max_length=150)
    turno: str = Field(max_length=60)

    model_config = {"from_attributes": True}
