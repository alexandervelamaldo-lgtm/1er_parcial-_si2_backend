"""request cost estimation

Revision ID: 004_request_cost_estimation
Revises: 003_multimodal_assignment_push
Create Date: 2026-04-12 00:00:01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "004_request_cost_estimation"
down_revision: str | None = "003_multimodal_assignment_push"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("solicitudes", sa.Column("es_carretera", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(
        "solicitudes",
        sa.Column("condicion_vehiculo", sa.Text(), nullable=False, server_default="Operativo con limitaciones"),
    )
    op.add_column("solicitudes", sa.Column("nivel_riesgo", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("solicitudes", sa.Column("costo_estimado", sa.Float(), nullable=True))
    op.add_column("solicitudes", sa.Column("costo_estimado_min", sa.Float(), nullable=True))
    op.add_column("solicitudes", sa.Column("costo_estimado_max", sa.Float(), nullable=True))
    op.add_column("solicitudes", sa.Column("costo_estimacion_confianza", sa.Float(), nullable=True))
    op.add_column("solicitudes", sa.Column("costo_estimacion_nota", sa.Text(), nullable=True))

    op.execute("UPDATE solicitudes SET es_carretera = false WHERE es_carretera IS NULL")
    op.execute(
        "UPDATE solicitudes SET condicion_vehiculo = 'Operativo con limitaciones' WHERE condicion_vehiculo IS NULL"
    )
    op.execute("UPDATE solicitudes SET nivel_riesgo = 1 WHERE nivel_riesgo IS NULL")

    op.alter_column("solicitudes", "es_carretera", server_default=None)
    op.alter_column("solicitudes", "condicion_vehiculo", server_default=None)
    op.alter_column("solicitudes", "nivel_riesgo", server_default=None)


def downgrade() -> None:
    op.drop_column("solicitudes", "costo_estimacion_nota")
    op.drop_column("solicitudes", "costo_estimacion_confianza")
    op.drop_column("solicitudes", "costo_estimado_max")
    op.drop_column("solicitudes", "costo_estimado_min")
    op.drop_column("solicitudes", "costo_estimado")
    op.drop_column("solicitudes", "nivel_riesgo")
    op.drop_column("solicitudes", "condicion_vehiculo")
    op.drop_column("solicitudes", "es_carretera")
