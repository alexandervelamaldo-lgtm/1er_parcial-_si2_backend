from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Vehiculo(Base):
    __tablename__ = "vehiculos"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    marca: Mapped[str] = mapped_column(String(100), nullable=False)
    modelo: Mapped[str] = mapped_column(String(100), nullable=False)
    anio: Mapped[int] = mapped_column(Integer, nullable=False)
    placa: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(50), nullable=False)
    tipo_combustible: Mapped[str] = mapped_column(String(50), nullable=False)

    cliente = relationship("Cliente", back_populates="vehiculos", lazy="selectin")
    solicitudes = relationship("Solicitud", back_populates="vehiculo")
