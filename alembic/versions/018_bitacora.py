"""create bitacora table for per-tenant user action audit log

Crea la tabla `bitacora`, que registra toda acción mutante de un usuario
(crear/asignar/cancelar solicitudes, registrar pagos, aceptar propuestas…).
Se llena best-effort desde `TenantAuditMiddleware` tras una respuesta
exitosa. Vive en el schema/DB del tenant, por lo que el aislamiento es
automático. NUNCA guarda tokens ni secretos: solo método, ruta, acción
legible y el id del usuario.

Revision ID: 018_bitacora
Revises: 017_sync_idempotency
Create Date: 2026-06-05
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "018_bitacora"
down_revision: str | Sequence[str] | None = "017_sync_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bitacora",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Quién — id del usuario del JWT (nullable: rutas anónimas).
        sa.Column("user_id", sa.Integer(), nullable=True),
        # Qué — etiqueta legible en español.
        sa.Column("accion", sa.String(length=160), nullable=False),
        sa.Column("metodo", sa.String(length=8), nullable=False),
        sa.Column("ruta", sa.String(length=255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="0"),
        # Entidad afectada para filtrar ("solicitud", "taller", "pago"…).
        sa.Column("entidad", sa.String(length=64), nullable=True),
        sa.Column("entidad_id", sa.String(length=64), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("detalle", sa.Text(), nullable=True),
    )
    # Listado por defecto: "lo más reciente primero" → índice por created_at.
    op.create_index("ix_bitacora_created_at", "bitacora", ["created_at"])
    op.create_index("ix_bitacora_user_id", "bitacora", ["user_id"])
    op.create_index("ix_bitacora_entidad", "bitacora", ["entidad"])


def downgrade() -> None:
    op.drop_index("ix_bitacora_entidad", table_name="bitacora")
    op.drop_index("ix_bitacora_user_id", table_name="bitacora")
    op.drop_index("ix_bitacora_created_at", table_name="bitacora")
    op.drop_table("bitacora")
