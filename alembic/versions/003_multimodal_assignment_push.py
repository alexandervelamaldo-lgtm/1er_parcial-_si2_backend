"""multimodal assignment push

Revision ID: 003_multimodal_assignment_push
Revises: 002_workshop_finance_evidence
Create Date: 2026-04-05 00:00:01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "003_multimodal_assignment_push"
down_revision: str | None = "002_workshop_finance_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("solicitudes", sa.Column("etiquetas_ia", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("transcripcion_audio", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("proveedor_ia", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("cliente_aprobada", sa.Boolean(), nullable=True))
    op.add_column("solicitudes", sa.Column("cliente_aprobacion_observacion", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("cliente_aprobacion_fecha", sa.DateTime(timezone=True), nullable=True))
    op.add_column("solicitudes", sa.Column("propuesta_expira_en", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "user_device_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("plataforma", sa.String(length=30), nullable=False, server_default="mobile"),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "token", name="uq_user_device_token"),
    )
    op.create_index(op.f("ix_user_device_tokens_id"), "user_device_tokens", ["id"], unique=False)
    op.create_index(op.f("ix_user_device_tokens_user_id"), "user_device_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_device_tokens_user_id"), table_name="user_device_tokens")
    op.drop_index(op.f("ix_user_device_tokens_id"), table_name="user_device_tokens")
    op.drop_table("user_device_tokens")

    op.drop_column("solicitudes", "propuesta_expira_en")
    op.drop_column("solicitudes", "cliente_aprobacion_fecha")
    op.drop_column("solicitudes", "cliente_aprobacion_observacion")
    op.drop_column("solicitudes", "cliente_aprobada")
    op.drop_column("solicitudes", "proveedor_ia")
    op.drop_column("solicitudes", "transcripcion_audio")
    op.drop_column("solicitudes", "etiquetas_ia")
