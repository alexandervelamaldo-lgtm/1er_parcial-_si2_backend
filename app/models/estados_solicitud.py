from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EstadoSolicitud(Base):
    __tablename__ = "estados_solicitud"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)

    solicitudes = relationship("Solicitud", back_populates="estado")
