"""Bitácora de auditoría de acciones de usuario (por tenant).

A diferencia de `ia_audit_log` (telemetría económica de llamadas a la IA) y
de `historial_eventos` (cambios de estado de UNA solicitud), esta tabla
registra **toda acción mutante** que un usuario realiza sobre el sistema:
crear/asignar/cancelar solicitudes, registrar pagos, aceptar propuestas,
etc. Cada fila vive en el schema/DB del tenant, por lo que el aislamiento
multi-tenant es automático (la sesión ya está acotada al tenant).

Se llena de forma best-effort desde `TenantAuditMiddleware` después de que
la petición responde con éxito (status < 400). Nunca guarda tokens ni
secretos: solo método, ruta, acción legible y el id del usuario.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Bitacora(Base):
    __tablename__ = "bitacora"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )
    # Quién — id del usuario extraído del JWT. Nullable porque algunas rutas
    # mutantes son anónimas (p. ej. registro). El email se resuelve por JOIN
    # en el endpoint de consulta para no duplicar datos ni desincronizarlos.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Qué — etiqueta legible en español ("Cambió el estado de una solicitud").
    accion: Mapped[str] = mapped_column(String(160), nullable=False)
    # Detalle técnico para trazabilidad / filtros.
    metodo: Mapped[str] = mapped_column(String(8), nullable=False)
    ruta: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Entidad afectada — derivada de la ruta ("solicitud", "taller", "pago"…)
    # para poder filtrar la bitácora por tipo de objeto.
    entidad: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    entidad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detalle: Mapped[str | None] = mapped_column(Text, nullable=True)
