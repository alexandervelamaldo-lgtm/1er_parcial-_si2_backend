from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Tecnico(Base):
    __tablename__ = "tecnicos"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    taller_id: Mapped[int | None] = mapped_column(ForeignKey("talleres.id", ondelete="SET NULL"), nullable=True, index=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    telefono: Mapped[str] = mapped_column(String(30), nullable=False)
    especialidad: Mapped[str] = mapped_column(String(120), nullable=False)
    latitud_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitud_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    disponibilidad: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ubicacion_actualizada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)

    user = relationship("User", back_populates="tecnico", lazy="selectin")
    taller = relationship("Taller", back_populates="tecnicos", lazy="selectin")
    solicitudes = relationship("Solicitud", back_populates="tecnico")
