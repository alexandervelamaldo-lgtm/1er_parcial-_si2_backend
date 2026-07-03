"""on demand workshop services

Revision ID: 011_on_demand_workshop_services
Revises: 010_workshop_brand_discounts
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "011_on_demand_workshop_services"
down_revision: str | Sequence[str] | None = "010_workshop_brand_discounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tecnicos",
        sa.Column("radio_cobertura_km", sa.Float(), nullable=False, server_default=sa.text("25")),
    )
    op.add_column(
        "tecnicos",
        sa.Column("en_turno", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "servicios_taller_demanda",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("solicitud_id", sa.Integer(), nullable=False),
        sa.Column("taller_id", sa.Integer(), nullable=True),
        sa.Column("tecnico_id", sa.Integer(), nullable=True),
        sa.Column("estado", sa.String(length=50), nullable=False, server_default="BUSCANDO"),
        sa.Column("latitud_cliente", sa.Float(), nullable=False),
        sa.Column("longitud_cliente", sa.Float(), nullable=False),
        sa.Column("ubicacion_cliente_actualizada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("latitud_servicio", sa.Float(), nullable=False),
        sa.Column("longitud_servicio", sa.Float(), nullable=False),
        sa.Column("direccion_servicio", sa.Text(), nullable=True),
        sa.Column("radio_busqueda_km", sa.Float(), nullable=False, server_default=sa.text("25")),
        sa.Column("cobertura_tecnico_km", sa.Float(), nullable=True),
        sa.Column("distancia_asignacion_km", sa.Float(), nullable=True),
        sa.Column("eta_estimado_min", sa.Integer(), nullable=True),
        sa.Column("score_matching", sa.Float(), nullable=True),
        sa.Column("match_especialidad", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("detalle_matching", sa.Text(), nullable=True),
        sa.Column("confirmacion_ubicacion_ok", sa.Boolean(), nullable=True),
        sa.Column("latitud_confirmacion_final", sa.Float(), nullable=True),
        sa.Column("longitud_confirmacion_final", sa.Float(), nullable=True),
        sa.Column("distancia_confirmacion_m", sa.Float(), nullable=True),
        sa.Column("confirmacion_ubicacion_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["solicitud_id"], ["solicitudes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["taller_id"], ["talleres.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tecnico_id"], ["tecnicos.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("solicitud_id", name="uq_servicios_taller_demanda_solicitud_id"),
    )
    op.create_index(op.f("ix_servicios_taller_demanda_id"), "servicios_taller_demanda", ["id"], unique=False)
    op.create_index(op.f("ix_servicios_taller_demanda_solicitud_id"), "servicios_taller_demanda", ["solicitud_id"], unique=False)
    op.create_index(op.f("ix_servicios_taller_demanda_taller_id"), "servicios_taller_demanda", ["taller_id"], unique=False)
    op.create_index(op.f("ix_servicios_taller_demanda_tecnico_id"), "servicios_taller_demanda", ["tecnico_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_servicios_taller_demanda_tecnico_id"), table_name="servicios_taller_demanda")
    op.drop_index(op.f("ix_servicios_taller_demanda_taller_id"), table_name="servicios_taller_demanda")
    op.drop_index(op.f("ix_servicios_taller_demanda_solicitud_id"), table_name="servicios_taller_demanda")
    op.drop_index(op.f("ix_servicios_taller_demanda_id"), table_name="servicios_taller_demanda")
    op.drop_table("servicios_taller_demanda")
    op.drop_column("tecnicos", "en_turno")
    op.drop_column("tecnicos", "radio_cobertura_km")
