"""make categoria_dano required and add diagnostico_categoria to notifications

Revision ID: 019_diag_categoria
Revises: 018_bitacora
Create Date: 2026-06-05

Nota: el revision_id se acortó (de 019_diagnostico_categoria_required) porque
`alembic_version.version_num` es VARCHAR(32) y el id original tenía 34 chars,
lo que rompía `alembic upgrade head` con StringDataRightTruncationError.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "019_diag_categoria"
down_revision: str | Sequence[str] | None = "018_bitacora"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE solicitudes SET categoria_dano = 'general' WHERE categoria_dano IS NULL"))
    op.alter_column(
        "solicitudes",
        "categoria_dano",
        existing_type=sa.Text(),
        nullable=False,
        server_default="general",
    )
    op.create_index(op.f("ix_solicitudes_categoria_dano"), "solicitudes", ["categoria_dano"], unique=False)

    op.add_column("notificaciones", sa.Column("diagnostico_categoria", sa.String(length=80), nullable=True))
    op.create_index(
        op.f("ix_notificaciones_diagnostico_categoria"),
        "notificaciones",
        ["diagnostico_categoria"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notificaciones_diagnostico_categoria"), table_name="notificaciones")
    op.drop_column("notificaciones", "diagnostico_categoria")

    op.drop_index(op.f("ix_solicitudes_categoria_dano"), table_name="solicitudes")
    op.alter_column(
        "solicitudes",
        "categoria_dano",
        existing_type=sa.Text(),
        nullable=True,
        server_default=None,
    )

