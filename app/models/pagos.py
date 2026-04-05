from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PagoSolicitud(Base):
    __tablename__ = "pagos_solicitud"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id", ondelete="CASCADE"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    taller_id: Mapped[int | None] = mapped_column(ForeignKey("talleres.id", ondelete="SET NULL"), index=True, nullable=True)
    monto_total: Mapped[float] = mapped_column(Float, nullable=False)
    monto_comision: Mapped[float] = mapped_column(Float, nullable=False)
    monto_taller: Mapped[float] = mapped_column(Float, nullable=False)
    metodo_pago: Mapped[str] = mapped_column(String(40), nullable=False)
    estado: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDIENTE")
    referencia_externa: Mapped[str | None] = mapped_column(String(120), nullable=True)
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    fecha_pago: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    solicitud = relationship("Solicitud", back_populates="pagos", lazy="selectin")
    taller = relationship("Taller", back_populates="pagos", lazy="selectin")
    cliente = relationship("Cliente", lazy="selectin")
