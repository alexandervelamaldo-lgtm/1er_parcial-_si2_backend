from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_plane.database import ControlBase


class IncidentTenantKeyword(ControlBase):
    __tablename__ = "incident_tenant_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
