from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SyncIdempotencia(Base):
    """
    Registro persistente de operaciones offline ya procesadas por `/sync/lote`.

    Reemplaza la caché en memoria por proceso (`_IDEM_CACHE`) que NO sobrevive
    a reinicios ni funciona con múltiples instancias. En la nube (Render con
    réplicas o cold-start que apaga el contenedor) la caché en RAM se pierde y
    un reintento del cliente re-ejecutaría la misma operación → duplicados.

    Con esta tabla la garantía de idempotencia es:
      - Cross-process / cross-réplica: todas las instancias consultan la misma DB.
      - Durable: sobrevive reinicios y cold-starts.
      - Por-tenant: en modo schema-per-tenant la tabla vive en el schema del
        tenant; en modo database (default) vive junto a `solicitudes`.

    `resultado` guarda el JSON del resultado original para poder devolver la
    MISMA respuesta cuando llega un duplicado, sin re-ejecutar el handler.
    """

    __tablename__ = "sync_idempotencia"
    # Nombres de índice explícitos para que coincidan EXACTAMENTE con la
    # migración 017 (evita drift espurio si alguien corre --autogenerate).
    __table_args__ = (
        Index("ix_sync_idempotencia_key", "idempotency_key", unique=True),
        Index("ix_sync_idempotencia_creado_en", "creado_en"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    # Plain Integer (no FK): el registro de dedup debe sobrevivir aunque el
    # usuario se elimine — borrarlo en cascada reabriría la ventana a duplicados.
    usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resultado: Mapped[str] = mapped_column(Text, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
