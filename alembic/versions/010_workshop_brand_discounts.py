"""workshop brand discounts

Revision ID: 010_workshop_brand_discounts
Revises: 009_workshop_categories_routing
Create Date: 2026-05-20 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "010_workshop_brand_discounts"
down_revision: str | None = "009_workshop_categories_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "talleres",
        sa.Column(
            "descuentos_marca",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("talleres", "descuentos_marca")

