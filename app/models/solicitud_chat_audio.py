"""Adjuntos de audio para mensajes del chat de solicitud (notas de voz).

Se guardan como bytea directamente en Postgres para mantener el stack
simple (sin S3/Cloudinary) y sin superficie externa. Cada nota ronda
30-100 KB y el volumen esperado es bajo — con backup diario de la DB
alcanza. Si más adelante escala, migramos a object storage sin cambiar
el schema de la tabla principal.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SolicitudChatAudioAttachment(Base):
    __tablename__ = "solicitud_chat_audio_attachments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Un mensaje del chat puede tener a lo sumo un adjunto de audio.
    # `unique=True` refleja esa cardinalidad y previene duplicados.
    message_id: Mapped[int] = mapped_column(
        ForeignKey("solicitud_chat_messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # MIME real de lo que grabó el cliente: "audio/webm;codecs=opus" (web),
    # "audio/mp4" o "audio/aac" (móvil). Lo devolvemos tal cual al servir
    # para que el <audio>/audioplayers del receptor decida cómo decodear.
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Duración estimada en ms (calculada por el cliente al terminar de
    # grabar, ya que decodear server-side agrega latencia). Puede quedar
    # NULL si el cliente no la pudo calcular.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    message = relationship("SolicitudChatMessage", lazy="selectin", backref="audio_attachment")
