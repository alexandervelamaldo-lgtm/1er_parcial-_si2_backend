"""service closure and invoice fields

Revision ID: 005_service_closure_invoice
Revises: 004_request_cost_estimation
Create Date: 2026-04-18 00:00:02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "005_service_closure_invoice"
down_revision: str | None = "004_request_cost_estimation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("solicitudes", sa.Column("costo_final", sa.Float(), nullable=True))
    op.add_column("solicitudes", sa.Column("moneda_costo", sa.Text(), nullable=False, server_default="BOB"))
    op.add_column("solicitudes", sa.Column("trabajo_terminado", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("solicitudes", sa.Column("trabajo_terminado_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("solicitudes", sa.Column("trabajo_terminado_observacion", sa.Text(), nullable=True))

    op.execute("UPDATE solicitudes SET moneda_costo = 'BOB' WHERE moneda_costo IS NULL")
    op.execute("UPDATE solicitudes SET trabajo_terminado = false WHERE trabajo_terminado IS NULL")

    op.alter_column("solicitudes", "moneda_costo", server_default=None)
    op.alter_column("solicitudes", "trabajo_terminado", server_default=None)


def downgrade() -> None:
    op.drop_column("solicitudes", "trabajo_terminado_observacion")
    op.drop_column("solicitudes", "trabajo_terminado_en")
    op.drop_column("solicitudes", "trabajo_terminado")
    op.drop_column("solicitudes", "moneda_costo")
    op.drop_column("solicitudes", "costo_final")
