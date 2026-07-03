"""allow the same web push endpoint for multiple users

Revision ID: 020_web_push_endpoint_per_user
Revises: 019_diag_categoria
Create Date: 2026-06-05 00:00:00
"""

from collections.abc import Sequence

from alembic import op


revision: str = "020_web_push_endpoint_per_user"
down_revision: str | None = "019_diag_categoria"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_web_push_endpoint", "web_push_subscriptions", type_="unique")
    op.create_unique_constraint(
        "uq_web_push_user_endpoint",
        "web_push_subscriptions",
        ["user_id", "endpoint"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_web_push_user_endpoint", "web_push_subscriptions", type_="unique")
    op.create_unique_constraint("uq_web_push_endpoint", "web_push_subscriptions", ["endpoint"])
