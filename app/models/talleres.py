from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Taller(Base):
    __tablename__ = "talleres"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    direccion: Mapped[str] = mapped_column(String(255), nullable=False)
    latitud: Mapped[float] = mapped_column(Float, nullable=False)
    longitud: Mapped[float] = mapped_column(Float, nullable=False)
    telefono: Mapped[str] = mapped_column(String(30), nullable=False)
    capacidad: Mapped[int] = mapped_column(Integer, nullable=False)

    solicitudes = relationship("Solicitud", back_populates="taller")
