"""workshop finance evidence

Revision ID: 002_workshop_finance_evidence
Revises: 001_initial_schema
Create Date: 2026-04-05 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "002_workshop_finance_evidence"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("talleres", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("talleres", sa.Column("servicios", sa.Text(), server_default="", nullable=False))
    op.add_column("talleres", sa.Column("disponible", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("talleres", sa.Column("acepta_automaticamente", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_unique_constraint("uq_talleres_user_id", "talleres", ["user_id"])
    op.create_foreign_key("fk_talleres_user_id_users", "talleres", "users", ["user_id"], ["id"], ondelete="SET NULL")

    op.add_column("tecnicos", sa.Column("taller_id", sa.Integer(), nullable=True))
    op.add_column("tecnicos", sa.Column("ubicacion_actualizada_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True))
    op.create_index("ix_tecnicos_taller_id", "tecnicos", ["taller_id"])
    op.create_foreign_key("fk_tecnicos_taller_id_talleres", "tecnicos", "talleres", ["taller_id"], ["id"], ondelete="SET NULL")

    op.add_column("solicitudes", sa.Column("clasificacion_confianza", sa.Float(), nullable=True))
    op.add_column("solicitudes", sa.Column("requiere_revision_manual", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("solicitudes", sa.Column("motivo_prioridad", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("resumen_ia", sa.Text(), nullable=True))

    op.create_table(
        "evidencias_solicitud",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("nombre_archivo", sa.String(length=255), nullable=True),
        sa.Column("contenido_texto", sa.Text(), nullable=True),
        sa.Column("archivo_url", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("tamano_bytes", sa.Integer(), nullable=True),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_evidencias_solicitud_id", "evidencias_solicitud", ["id"])
    op.create_index("ix_evidencias_solicitud_solicitud_id", "evidencias_solicitud", ["solicitud_id"])

    op.create_table(
        "pagos_solicitud",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("taller_id", sa.Integer(), sa.ForeignKey("talleres.id", ondelete="SET NULL"), nullable=True),
        sa.Column("monto_total", sa.Float(), nullable=False),
        sa.Column("monto_comision", sa.Float(), nullable=False),
        sa.Column("monto_taller", sa.Float(), nullable=False),
        sa.Column("metodo_pago", sa.String(length=40), nullable=False),
        sa.Column("estado", sa.String(length=30), nullable=False),
        sa.Column("referencia_externa", sa.String(length=120), nullable=True),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("fecha_pago", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pagos_solicitud_id", "pagos_solicitud", ["id"])
    op.create_index("ix_pagos_solicitud_solicitud_id", "pagos_solicitud", ["solicitud_id"])
    op.create_index("ix_pagos_solicitud_cliente_id", "pagos_solicitud", ["cliente_id"])
    op.create_index("ix_pagos_solicitud_taller_id", "pagos_solicitud", ["taller_id"])

    op.create_table(
        "disputas_solicitud",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("motivo", sa.String(length=150), nullable=False),
        sa.Column("detalle", sa.Text(), nullable=False),
        sa.Column("estado", sa.String(length=30), nullable=False),
        sa.Column("resolucion", sa.Text(), nullable=True),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("fecha_resolucion", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_disputas_solicitud_id", "disputas_solicitud", ["id"])
    op.create_index("ix_disputas_solicitud_solicitud_id", "disputas_solicitud", ["solicitud_id"])
    op.create_index("ix_disputas_solicitud_usuario_id", "disputas_solicitud", ["usuario_id"])


def downgrade() -> None:
    op.drop_index("ix_disputas_solicitud_usuario_id", table_name="disputas_solicitud")
    op.drop_index("ix_disputas_solicitud_solicitud_id", table_name="disputas_solicitud")
    op.drop_index("ix_disputas_solicitud_id", table_name="disputas_solicitud")
    op.drop_table("disputas_solicitud")

    op.drop_index("ix_pagos_solicitud_taller_id", table_name="pagos_solicitud")
    op.drop_index("ix_pagos_solicitud_cliente_id", table_name="pagos_solicitud")
    op.drop_index("ix_pagos_solicitud_solicitud_id", table_name="pagos_solicitud")
    op.drop_index("ix_pagos_solicitud_id", table_name="pagos_solicitud")
    op.drop_table("pagos_solicitud")

    op.drop_index("ix_evidencias_solicitud_solicitud_id", table_name="evidencias_solicitud")
    op.drop_index("ix_evidencias_solicitud_id", table_name="evidencias_solicitud")
    op.drop_table("evidencias_solicitud")

    op.drop_column("solicitudes", "resumen_ia")
    op.drop_column("solicitudes", "motivo_prioridad")
    op.drop_column("solicitudes", "requiere_revision_manual")
    op.drop_column("solicitudes", "clasificacion_confianza")

    op.drop_constraint("fk_tecnicos_taller_id_talleres", "tecnicos", type_="foreignkey")
    op.drop_index("ix_tecnicos_taller_id", table_name="tecnicos")
    op.drop_column("tecnicos", "ubicacion_actualizada_en")
    op.drop_column("tecnicos", "taller_id")

    op.drop_constraint("fk_talleres_user_id_users", "talleres", type_="foreignkey")
    op.drop_constraint("uq_talleres_user_id", "talleres", type_="unique")
    op.drop_column("talleres", "acepta_automaticamente")
    op.drop_column("talleres", "disponible")
    op.drop_column("talleres", "servicios")
    op.drop_column("talleres", "user_id")
