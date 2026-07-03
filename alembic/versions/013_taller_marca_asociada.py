"""taller marca asociada and cotizacion brand discount

Revision ID: 013_taller_marca_asociada
Revises: 012_ai_consent_and_logs
Create Date: 2026-05-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "013_taller_marca_asociada"
down_revision: str | Sequence[str] | None = "012_ai_consent_and_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add marca_asociada to talleres — nullable, max 100 chars
    op.add_column(
        "talleres",
        sa.Column("marca_asociada", sa.String(100), nullable=True),
    )

    # Add discount fields to cotizaciones
    op.add_column(
        "cotizaciones",
        sa.Column("descuento_marca_pct", sa.Float(), nullable=True),
    )
    op.add_column(
        "cotizaciones",
        sa.Column(
            "total_final",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("cotizaciones", "total_final")
    op.drop_column("cotizaciones", "descuento_marca_pct")
    op.drop_column("talleres", "marca_asociada")
