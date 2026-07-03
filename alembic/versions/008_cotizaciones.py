"""cotizaciones

Revision ID: 008_cotizaciones
Revises: 007_web_push_and_preferences
Create Date: 2026-05-19 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "008_cotizaciones"
down_revision: str | None = "007_web_push_and_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cotizaciones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("solicitud_id", sa.Integer(), nullable=False),
        sa.Column("taller_id", sa.Integer(), nullable=True),
        sa.Column("tecnico_id", sa.Integer(), nullable=True),
        sa.Column("estado", sa.String(length=30), nullable=False, server_default="BORRADOR"),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("moneda", sa.String(length=8), nullable=False, server_default="BOB"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["solicitud_id"], ["solicitudes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["taller_id"], ["talleres.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tecnico_id"], ["tecnicos.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("solicitud_id", name="uq_cotizacion_solicitud"),
    )
    op.create_index(op.f("ix_cotizaciones_id"), "cotizaciones", ["id"], unique=False)
    op.create_index(op.f("ix_cotizaciones_solicitud_id"), "cotizaciones", ["solicitud_id"], unique=False)
    op.create_index(op.f("ix_cotizaciones_taller_id"), "cotizaciones", ["taller_id"], unique=False)
    op.create_index(op.f("ix_cotizaciones_tecnico_id"), "cotizaciones", ["tecnico_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cotizaciones_tecnico_id"), table_name="cotizaciones")
    op.drop_index(op.f("ix_cotizaciones_taller_id"), table_name="cotizaciones")
    op.drop_index(op.f("ix_cotizaciones_solicitud_id"), table_name="cotizaciones")
    op.drop_index(op.f("ix_cotizaciones_id"), table_name="cotizaciones")
    op.drop_table("cotizaciones")

