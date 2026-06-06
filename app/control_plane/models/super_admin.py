"""Modelo SuperAdmin del control plane.

Vive ÚNICAMENTE en la control DB. No tiene relación con la tabla `users`
de los tenants — son entidades conceptualmente distintas. Un super-admin
NO es un usuario de una organización; gestiona las organizaciones desde
afuera.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_plane.database import ControlBase


class SuperAdmin(ControlBase):
    __tablename__ = "super_admins"

    # Integer (no BigInteger) para que SQLite — usado en tests — pueda
    # auto-incrementar. No vamos a tener millones de super-admins; un
    # int de 32 bits sobra.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
