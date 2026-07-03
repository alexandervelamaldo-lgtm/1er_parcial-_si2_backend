from pydantic import BaseModel, Field


class VehiculoBase(BaseModel):
    cliente_id: int
    marca: str = Field(min_length=2, max_length=100)
    modelo: str = Field(min_length=1, max_length=100)
    anio: int = Field(ge=1950, le=2100)
    placa: str = Field(min_length=5, max_length=20)
    color: str = Field(min_length=3, max_length=50)
    tipo_combustible: str = Field(min_length=3, max_length=50)


class VehiculoCreate(VehiculoBase):
    pass


class VehiculoUpdate(BaseModel):
    marca: str | None = Field(default=None, min_length=2, max_length=100)
    modelo: str | None = Field(default=None, min_length=1, max_length=100)
    anio: int | None = Field(default=None, ge=1950, le=2100)
    placa: str | None = Field(default=None, min_length=5, max_length=20)
    color: str | None = Field(default=None, min_length=3, max_length=50)
    tipo_combustible: str | None = Field(default=None, min_length=3, max_length=50)


class VehiculoResponse(VehiculoBase):
    id: int

    model_config = {"from_attributes": True}
