"""Modelo de auditoría granular de llamadas a la IA (OpenAI/Gemini).

Cada llamada a visión, audio o estimador de costo persiste una fila aquí
con métricas: tokens, latencia, costo USD, confianza, fallback. Difiere
de `ai_request_logs` en que captura *unidades económicas* (tokens, USD)
y es por-llamada-de-IA, no por-petición-HTTP. Sirve para:

  - calibrar precisión vs facturación real del taller,
  - alertar si OpenAI/Gemini está caída (rachas de fallback=True),
  - reporting de costos en `cost_usd` agrupado por día/tipo.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class IaAuditLog(Base):
    __tablename__ = "ia_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    solicitud_id: Mapped[int | None] = mapped_column(
        ForeignKey("solicitudes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tipo: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confianza: Mapped[float | None] = mapped_column(Float, nullable=True)
    fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    solicitud = relationship("Solicitud")
