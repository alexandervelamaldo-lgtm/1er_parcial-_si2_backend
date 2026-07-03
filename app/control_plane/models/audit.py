"""Auditoría de acciones del super-admin.

Cada operación destructiva o de creación cross-tenant deja un registro
aquí: quién, qué hizo, sobre qué tenant. Sirve para investigar
incidentes (ej. "¿quién suspendió el tenant default ayer a las 3am?").
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.control_plane.database import ControlBase


class SuperAdminAuditLog(ControlBase):
    __tablename__ = "super_admin_audit_log"

    # Integer (no BigInteger) por compatibilidad con SQLite en tests.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    super_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("super_admins.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # email duplicado para que el registro siga siendo legible aunque
    # el super-admin haya sido borrado.
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_tenant: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_resource: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Detalle libre — JSON serializado como texto para evitar acoplar
    # a postgres JSONB (la control DB podría ser SQLite en tests).
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    super_admin = relationship("SuperAdmin")
