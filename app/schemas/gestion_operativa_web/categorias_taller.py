from pydantic import BaseModel, Field


class CategoriaTallerResponse(BaseModel):
    id: int
    slug: str = Field(min_length=1, max_length=80)
    nombre: str = Field(min_length=1, max_length=120)
    descripcion: str | None = None

    model_config = {"from_attributes": True}

