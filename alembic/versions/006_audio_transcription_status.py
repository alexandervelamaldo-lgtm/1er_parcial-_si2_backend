"""audio transcription status fields

Revision ID: 006_audio_transcription_status
Revises: 005_service_closure_invoice
Create Date: 2026-04-19 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "006_audio_transcription_status"
down_revision: str | None = "005_service_closure_invoice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("solicitudes", sa.Column("transcripcion_audio_estado", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("transcripcion_audio_error", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("transcripcion_audio_actualizada_en", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("solicitudes", "transcripcion_audio_actualizada_en")
    op.drop_column("solicitudes", "transcripcion_audio_error")
    op.drop_column("solicitudes", "transcripcion_audio_estado")

