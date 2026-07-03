from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DisputaSolicitud(Base):
    __tablename__ = "disputas_solicitud"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    motivo: Mapped[str] = mapped_column(String(150), nullable=False)
    detalle: Mapped[str] = mapped_column(Text, nullable=False)
    estado: Mapped[str] = mapped_column(String(30), nullable=False, default="ABIERTA")
    resolucion: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    fecha_resolucion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    solicitud = relationship("Solicitud", back_populates="disputas", lazy="selectin")
    usuario = relationship("User", lazy="selectin")
