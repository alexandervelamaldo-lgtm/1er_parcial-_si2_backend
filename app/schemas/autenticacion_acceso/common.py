from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MensajeResponse(BaseModel):
    message: str


class TimestampedResponse(ORMBaseModel):
    created_at: datetime | None = None
    updated_at: datetime | None = None
