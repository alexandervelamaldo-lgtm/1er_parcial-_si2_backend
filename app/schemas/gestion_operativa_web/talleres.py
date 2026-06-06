from pydantic import BaseModel, Field, field_validator

from app.schemas.gestion_operativa_web.categorias_taller import CategoriaTallerResponse


class TallerBase(BaseModel):
    nombre: str = Field(min_length=3, max_length=150)
    direccion: str = Field(min_length=5, max_length=255)
    latitud: float = Field(ge=-90, le=90)
    longitud: float = Field(ge=-180, le=180)
    telefono: str = Field(min_length=7, max_length=30)
    horarios: str | None = None
    certificaciones: str | None = None
    tarifas_base: dict = Field(default_factory=dict)
    descuentos_marca: dict[str, float] = Field(default_factory=dict)
    marca_asociada: str | None = Field(default=None, max_length=100)
    rating_promedio: float = 0.0
    rating_total: int = 0
    capacidad: int = Field(ge=1, le=1000)
    servicios: list[str] = Field(default_factory=list)
    disponible: bool = True
    acepta_automaticamente: bool = False
    categoria: CategoriaTallerResponse | None = None

    @field_validator("marca_asociada")
    @classmethod
    def validate_marca_asociada(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = str(v).strip().upper()
        return value or None

    @field_validator("descuentos_marca")
    @classmethod
    def validate_descuentos_marca(cls, v: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for raw_key, raw_value in (v or {}).items():
            key = str(raw_key or "").strip().upper()
            if not key:
                continue
            try:
                value = float(raw_value)
            except Exception as exc:
                raise ValueError("Los descuentos por marca deben ser numéricos") from exc
            if value < 0 or value > 100:
                raise ValueError("Los descuentos por marca deben estar entre 0 y 100")
            normalized[key] = round(value, 2)
        return normalized


class TallerUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=150)
    direccion: str | None = Field(default=None, min_length=5, max_length=255)
    latitud: float | None = Field(default=None, ge=-90, le=90)
    longitud: float | None = Field(default=None, ge=-180, le=180)
    telefono: str | None = Field(default=None, min_length=7, max_length=30)
    horarios: str | None = None
    certificaciones: str | None = None
    tarifas_base: dict | None = None
    descuentos_marca: dict[str, float] | None = None
    marca_asociada: str | None = Field(default=None, max_length=100)
    rating_promedio: float | None = None
    rating_total: int | None = None
    capacidad: int | None = Field(default=None, ge=1, le=1000)
    servicios: list[str] | None = None
    disponible: bool | None = None
    acepta_automaticamente: bool | None = None
    categoria_id: int | None = None

    @field_validator("marca_asociada")
    @classmethod
    def validate_marca_asociada_update(cls, v: str | None) -> str | None:
        return TallerBase.validate_marca_asociada(v)

    @field_validator("descuentos_marca")
    @classmethod
    def validate_descuentos_marca_update(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return None
        return TallerBase.validate_descuentos_marca(v)


class TallerAdminCreate(BaseModel):
    categoria_id: int
    nombre: str = Field(min_length=3, max_length=150)
    direccion: str = Field(min_length=5, max_length=255)
    latitud: float = Field(ge=-90, le=90)
    longitud: float = Field(ge=-180, le=180)
    telefono: str = Field(min_length=7, max_length=30)
    horarios: str | None = None
    certificaciones: str | None = None
    tarifas_base: dict = Field(default_factory=dict)
    descuentos_marca: dict[str, float] = Field(default_factory=dict)
    marca_asociada: str | None = Field(default=None, max_length=100)
    rating_promedio: float = 0.0
    rating_total: int = 0
    capacidad: int = Field(ge=1, le=1000)
    servicios: list[str] = Field(default_factory=list)
    disponible: bool = True
    acepta_automaticamente: bool = False
    email: str | None = None
    password: str | None = None

    @field_validator("marca_asociada")
    @classmethod
    def validate_marca_asociada_create(cls, v: str | None) -> str | None:
        return TallerBase.validate_marca_asociada(v)

    @field_validator("descuentos_marca")
    @classmethod
    def validate_descuentos_marca_create(cls, v: dict[str, float]) -> dict[str, float]:
        return TallerBase.validate_descuentos_marca(v)


class TallerResponse(TallerBase):
    id: int
    user_id: int | None = None
    distancia_km: float | None = None
    score: float | None = None
    match_especializacion: bool = False
    motivo_sugerencia: str | None = None

    model_config = {"from_attributes": True}


class TallerMapaResponse(TallerResponse):
    presupuesto_min: float | None = None
    presupuesto_max: float | None = None
    presupuesto_descuento_min: float | None = None
    presupuesto_descuento_max: float | None = None
    descuento_porcentaje_aplicado: float | None = None
    tiempo_reparacion_horas: float | None = None
