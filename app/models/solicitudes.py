from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import PrioridadSolicitud


class Solicitud(Base):
    __tablename__ = "solicitudes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    vehiculo_id: Mapped[int] = mapped_column(ForeignKey("vehiculos.id", ondelete="CASCADE"), index=True)
    tecnico_id: Mapped[int | None] = mapped_column(ForeignKey("tecnicos.id", ondelete="SET NULL"), nullable=True)
    taller_id: Mapped[int | None] = mapped_column(ForeignKey("talleres.id", ondelete="SET NULL"), nullable=True)
    tipo_incidente_id: Mapped[int] = mapped_column(ForeignKey("tipos_incidente.id"), index=True)
    estado_id: Mapped[int] = mapped_column(ForeignKey("estados_solicitud.id"), index=True)
    latitud_incidente: Mapped[float] = mapped_column(Float, nullable=False)
    longitud_incidente: Mapped[float] = mapped_column(Float, nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    foto_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    prioridad: Mapped[PrioridadSolicitud] = mapped_column(
        Enum(PrioridadSolicitud, name="prioridad_solicitud"),
        nullable=False,
    )
    fecha_solicitud: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    fecha_asignacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fecha_atencion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fecha_cierre: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cliente = relationship("Cliente", back_populates="solicitudes", lazy="selectin")
    vehiculo = relationship("Vehiculo", back_populates="solicitudes", lazy="selectin")
    tecnico = relationship("Tecnico", back_populates="solicitudes", lazy="selectin")
    taller = relationship("Taller", back_populates="solicitudes", lazy="selectin")
    tipo_incidente = relationship("TipoIncidente", back_populates="solicitudes", lazy="selectin")
    estado = relationship("EstadoSolicitud", back_populates="solicitudes", lazy="selectin")
    historial = relationship("HistorialEvento", back_populates="solicitud", cascade="all, delete-orphan")
