"""Chat en vivo cliente ↔ técnico durante una solicitud activa.

Cada mensaje queda persistido para poder hidratar el hilo cuando el
cliente/técnico reabra el detalle de la solicitud, y para auditoría.

Vive en la DB de cada tenant (per-tenant), no en el control plane.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SolicitudChatMessage(Base):
    __tablename__ = "solicitud_chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    solicitud_id: Mapped[int] = mapped_column(
        ForeignKey("solicitudes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "cliente" | "tecnico". No es un enum de Postgres para evitar migraciones
    # al agregar variantes futuras (ej. "operador" cuando escalen). Se valida
    # a nivel del router.
    sender_role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    # NULL hasta que el otro lado marque como leído. Permite badge de
    # "no leídos" en el detalle sin tener que hacer join complejo.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sender = relationship("User", lazy="selectin")
    solicitud = relationship("Solicitud", lazy="selectin")

    __table_args__ = (
        # Búsqueda típica: mensajes de una solicitud ordenados por fecha.
        Index("ix_solicitud_chat_msg_solicitud_created", "solicitud_id", "created_at"),
    )
