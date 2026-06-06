from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AiConsentRequest(BaseModel):
    consent: bool


class AiConsentResponse(BaseModel):
    consent: bool
    consented_at: datetime | None = None


AiTextAction = Literal["redactar", "corregir", "traducir", "resumir"]


class AiTextGenerateRequest(BaseModel):
    action: AiTextAction
    text: str = Field(min_length=1, max_length=6000)
    target_language: str | None = Field(default=None, max_length=40)
    tone: str | None = Field(default=None, max_length=40)
    length: str | None = Field(default=None, max_length=40)


class AiTextGenerateResponse(BaseModel):
    output: str
    provider: str
    latency_ms: int


class AiVoiceIntentRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)
    context: dict[str, Any] | None = None


class AiVoiceIntentResponse(BaseModel):
    action: str
    confidence: float
    parameters: dict[str, Any] = {}
    reply: str
    provider: str
    latency_ms: int


class AiImageAnalyzeResponse(BaseModel):
    allowed: bool
    categories: list[str] = []
    reason: str | None = None
    ocr_text: str | None = None
    alt_text: str | None = None
    labels: list[str] = []
    severity: str | None = None
    provider: str
    latency_ms: int


class AiVoiceReportNarrationRequest(BaseModel):
    report_name: str = Field(min_length=3, max_length=80)
    period: str = Field(default="today", min_length=2, max_length=40)
    format: str = Field(default="pdf", min_length=2, max_length=20)
    audience: str = Field(default="operaciones", min_length=2, max_length=60)
    highlights: list[str] = Field(default_factory=list, max_length=12)
    stats: dict[str, Any] = Field(default_factory=dict)


class AiVoiceReportNarrationResponse(BaseModel):
    narration: str
    provider: str
    latency_ms: int
