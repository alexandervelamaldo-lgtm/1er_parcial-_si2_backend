from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EvidenciaSolicitud(Base):
    __tablename__ = "evidencias_solicitud"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    nombre_archivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contenido_texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    archivo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tamano_bytes: Mapped[int | None] = mapped_column(nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    solicitud = relationship("Solicitud", back_populates="evidencias", lazy="selectin")
    usuario = relationship("User", lazy="selectin")
