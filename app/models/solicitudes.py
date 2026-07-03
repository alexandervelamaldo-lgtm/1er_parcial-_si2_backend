from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
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
    es_carretera: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    condicion_vehiculo: Mapped[str] = mapped_column(Text, nullable=False, default="Operativo con limitaciones")
    nivel_riesgo: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    clasificacion_confianza: Mapped[float | None] = mapped_column(Float, nullable=True)
    requiere_revision_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    motivo_prioridad: Mapped[str | None] = mapped_column(Text, nullable=True)
    resumen_ia: Mapped[str | None] = mapped_column(Text, nullable=True)
    etiquetas_ia: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcripcion_audio: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcripcion_audio_estado: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcripcion_audio_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcripcion_audio_actualizada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proveedor_ia: Mapped[str | None] = mapped_column(Text, nullable=True)
    costo_estimado: Mapped[float | None] = mapped_column(Float, nullable=True)
    costo_estimado_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    costo_estimado_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    costo_estimacion_confianza: Mapped[float | None] = mapped_column(Float, nullable=True)
    costo_estimacion_nota: Mapped[str | None] = mapped_column(Text, nullable=True)
    costo_final: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneda_costo: Mapped[str] = mapped_column(Text, nullable=False, default="BOB")
    trabajo_terminado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trabajo_terminado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trabajo_terminado_observacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    cliente_aprobada: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cliente_aprobacion_observacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    cliente_aprobacion_fecha: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    propuesta_expira_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cuenta cuántos talleres consecutivos rechazaron la propuesta del
    # cliente. Cuando llega a 3 el backend escala a operadores. Se
    # resetea al aceptar (taller acepta) o al re-elegir (cuenta queda en 0
    # solo al cancelar — el contador en sí persiste durante todo el ciclo).
    taller_rechazos_consecutivos: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
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
    fecha_incidente: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    danos_descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    ubicacion_texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    categoria_dano: Mapped[str] = mapped_column(Text, nullable=False, default="general", server_default="general")
    presupuesto_aceptado: Mapped[float | None] = mapped_column(Float, nullable=True)
    ruta_osrm: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ruta_distancia_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    ruta_eta_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    cliente = relationship("Cliente", back_populates="solicitudes", lazy="selectin")
    vehiculo = relationship("Vehiculo", back_populates="solicitudes", lazy="selectin")
    tecnico = relationship("Tecnico", back_populates="solicitudes", lazy="selectin")
    taller = relationship("Taller", back_populates="solicitudes", lazy="selectin")
    tipo_incidente = relationship("TipoIncidente", back_populates="solicitudes", lazy="selectin")
    estado = relationship("EstadoSolicitud", back_populates="solicitudes", lazy="selectin")
    servicio_demanda = relationship(
        "ServicioTallerDemanda",
        back_populates="solicitud",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    historial = relationship("HistorialEvento", back_populates="solicitud", cascade="all, delete-orphan")
    evidencias = relationship("EvidenciaSolicitud", back_populates="solicitud", cascade="all, delete-orphan")
    pagos = relationship("PagoSolicitud", back_populates="solicitud", cascade="all, delete-orphan")
    disputas = relationship("DisputaSolicitud", back_populates="solicitud", cascade="all, delete-orphan")
