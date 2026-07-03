"""chat en vivo cliente-tecnico durante solicitud activa

Revision ID: 021_solicitud_chat_messages
Revises: 020_web_push_endpoint_per_user
Create Date: 2026-07-02 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "021_solicitud_chat_messages"
down_revision: str | None = "020_web_push_endpoint_per_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solicitud_chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "solicitud_id",
            sa.Integer(),
            sa.ForeignKey("solicitudes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender_role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_solicitud_chat_messages_solicitud_id",
        "solicitud_chat_messages",
        ["solicitud_id"],
    )
    op.create_index(
        "ix_solicitud_chat_messages_sender_user_id",
        "solicitud_chat_messages",
        ["sender_user_id"],
    )
    op.create_index(
        "ix_solicitud_chat_messages_created_at",
        "solicitud_chat_messages",
        ["created_at"],
    )
    op.create_index(
        "ix_solicitud_chat_msg_solicitud_created",
        "solicitud_chat_messages",
        ["solicitud_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_solicitud_chat_msg_solicitud_created", table_name="solicitud_chat_messages")
    op.drop_index("ix_solicitud_chat_messages_created_at", table_name="solicitud_chat_messages")
    op.drop_index("ix_solicitud_chat_messages_sender_user_id", table_name="solicitud_chat_messages")
    op.drop_index("ix_solicitud_chat_messages_solicitud_id", table_name="solicitud_chat_messages")
    op.drop_table("solicitud_chat_messages")
