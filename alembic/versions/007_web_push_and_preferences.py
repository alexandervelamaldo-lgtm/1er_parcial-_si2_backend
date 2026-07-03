"""web push subscriptions and notification preferences

Revision ID: 007_web_push_and_preferences
Revises: 006_audio_transcription_status
Create Date: 2026-04-26 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "007_web_push_and_preferences"
down_revision: str | None = "006_audio_transcription_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_push_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.String(length=1024), nullable=False),
        sa.Column("p256dh", sa.String(length=256), nullable=False),
        sa.Column("auth", sa.String(length=256), nullable=False),
        sa.Column("expiration_time", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint", name="uq_web_push_endpoint"),
    )
    op.create_index(op.f("ix_web_push_subscriptions_id"), "web_push_subscriptions", ["id"], unique=False)
    op.create_index(op.f("ix_web_push_subscriptions_user_id"), "web_push_subscriptions", ["user_id"], unique=False)

    op.create_table(
        "user_notification_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("disabled_all", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disabled_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_notification_preferences_user_id"),
    )
    op.create_index(op.f("ix_user_notification_preferences_id"), "user_notification_preferences", ["id"], unique=False)
    op.create_index(op.f("ix_user_notification_preferences_user_id"), "user_notification_preferences", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_notification_preferences_user_id"), table_name="user_notification_preferences")
    op.drop_index(op.f("ix_user_notification_preferences_id"), table_name="user_notification_preferences")
    op.drop_table("user_notification_preferences")

    op.drop_index(op.f("ix_web_push_subscriptions_user_id"), table_name="web_push_subscriptions")
    op.drop_index(op.f("ix_web_push_subscriptions_id"), table_name="web_push_subscriptions")
    op.drop_table("web_push_subscriptions")

