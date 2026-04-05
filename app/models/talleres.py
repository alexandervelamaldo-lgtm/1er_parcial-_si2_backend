from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Taller(Base):
    __tablename__ = "talleres"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True, nullable=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    direccion: Mapped[str] = mapped_column(String(255), nullable=False)
    latitud: Mapped[float] = mapped_column(Float, nullable=False)
    longitud: Mapped[float] = mapped_column(Float, nullable=False)
    telefono: Mapped[str] = mapped_column(String(30), nullable=False)
    capacidad: Mapped[int] = mapped_column(Integer, nullable=False)
    servicios: Mapped[str] = mapped_column(Text, default="", nullable=False)
    disponible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    acepta_automaticamente: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="taller", lazy="selectin")
    tecnicos = relationship("Tecnico", back_populates="taller")
    solicitudes = relationship("Solicitud", back_populates="taller")
    pagos = relationship("PagoSolicitud", back_populates="taller")
