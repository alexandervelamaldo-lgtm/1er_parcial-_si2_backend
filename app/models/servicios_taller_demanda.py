from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ServicioTallerDemanda(Base):
    __tablename__ = "servicios_taller_demanda"
    __table_args__ = (UniqueConstraint("solicitud_id", name="uq_servicios_taller_demanda_solicitud_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    solicitud_id: Mapped[int] = mapped_column(ForeignKey("solicitudes.id", ondelete="CASCADE"), index=True, nullable=False)
    taller_id: Mapped[int | None] = mapped_column(ForeignKey("talleres.id", ondelete="SET NULL"), index=True, nullable=True)
    tecnico_id: Mapped[int | None] = mapped_column(ForeignKey("tecnicos.id", ondelete="SET NULL"), index=True, nullable=True)
    estado: Mapped[str] = mapped_column(String(50), nullable=False, default="BUSCANDO")
    latitud_cliente: Mapped[float] = mapped_column(Float, nullable=False)
    longitud_cliente: Mapped[float] = mapped_column(Float, nullable=False)
    ubicacion_cliente_actualizada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    latitud_servicio: Mapped[float] = mapped_column(Float, nullable=False)
    longitud_servicio: Mapped[float] = mapped_column(Float, nullable=False)
    direccion_servicio: Mapped[str | None] = mapped_column(Text, nullable=True)
    radio_busqueda_km: Mapped[float] = mapped_column(Float, nullable=False, default=25.0)
    cobertura_tecnico_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    distancia_asignacion_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    eta_estimado_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_matching: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_especialidad: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detalle_matching: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmacion_ubicacion_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latitud_confirmacion_final: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitud_confirmacion_final: Mapped[float | None] = mapped_column(Float, nullable=True)
    distancia_confirmacion_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmacion_ubicacion_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    solicitud = relationship("Solicitud", back_populates="servicio_demanda", lazy="selectin")
    taller = relationship("Taller", lazy="selectin")
    tecnico = relationship("Tecnico", lazy="selectin")
