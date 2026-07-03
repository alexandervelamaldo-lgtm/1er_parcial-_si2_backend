"""ai consent and logs

Revision ID: 012_ai_consent_and_logs
Revises: 011_on_demand_workshop_services
Create Date: 2026-05-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "012_ai_consent_and_logs"
down_revision: str | Sequence[str] | None = "011_on_demand_workshop_services"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("ai_consent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("ai_consent_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "ai_request_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_request_logs_id"), "ai_request_logs", ["id"], unique=False)
    op.create_index(op.f("ix_ai_request_logs_user_id"), "ai_request_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_ai_request_logs_kind"), "ai_request_logs", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_request_logs_kind"), table_name="ai_request_logs")
    op.drop_index(op.f("ix_ai_request_logs_user_id"), table_name="ai_request_logs")
    op.drop_index(op.f("ix_ai_request_logs_id"), table_name="ai_request_logs")
    op.drop_table("ai_request_logs")
    op.drop_column("users", "ai_consent_at")
    op.drop_column("users", "ai_consent")
