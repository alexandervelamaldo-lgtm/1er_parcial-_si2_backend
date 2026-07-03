from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Cotizacion(Base):
    __tablename__ = "cotizaciones"
    __table_args__ = (UniqueConstraint("solicitud_id", name="uq_cotizacion_solicitud"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id", ondelete="CASCADE"), index=True)
    taller_id: Mapped[int | None] = mapped_column(ForeignKey("talleres.id", ondelete="SET NULL"), nullable=True, index=True)
    tecnico_id: Mapped[int | None] = mapped_column(ForeignKey("tecnicos.id", ondelete="SET NULL"), nullable=True, index=True)
    estado: Mapped[str] = mapped_column(String(30), default="BORRADOR", nullable=False)
    items: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    descuento_marca_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_final: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    moneda: Mapped[str] = mapped_column(String(8), default="BOB", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    solicitud = relationship("Solicitud", lazy="selectin")
    taller = relationship("Taller", lazy="selectin")
    tecnico = relationship("Tecnico", lazy="selectin")

