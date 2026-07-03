from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class HistorialEvento(Base):
    __tablename__ = "historial_eventos"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id", ondelete="CASCADE"), index=True)
    estado_anterior: Mapped[str] = mapped_column(String(60), nullable=False)
    estado_nuevo: Mapped[str] = mapped_column(String(60), nullable=False)
    observacion: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_evento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    solicitud = relationship("Solicitud", back_populates="historial", lazy="selectin")
    usuario = relationship("User", back_populates="eventos", lazy="selectin")
