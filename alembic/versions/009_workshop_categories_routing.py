"""workshop categories and routing fields

Revision ID: 009_workshop_categories_routing
Revises: 008_cotizaciones
Create Date: 2026-05-20 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "009_workshop_categories_routing"
down_revision: str | None = "008_cotizaciones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categorias_taller",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_categorias_taller_slug"),
    )
    op.create_index(op.f("ix_categorias_taller_id"), "categorias_taller", ["id"], unique=False)
    op.create_index(op.f("ix_categorias_taller_slug"), "categorias_taller", ["slug"], unique=True)

    op.execute(
        sa.text(
            "INSERT INTO categorias_taller (slug, nombre, descripcion) "
            "VALUES (:slug, :nombre, :descripcion) "
            "ON CONFLICT (slug) DO NOTHING"
        ).bindparams(
            slug="general",
            nombre="General",
            descripcion="Sin categoría asignada",
        )
    )

    op.add_column("talleres", sa.Column("categoria_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_talleres_categoria_id"), "talleres", ["categoria_id"], unique=False)
    op.create_foreign_key(
        "fk_talleres_categoria_id_categorias_taller",
        "talleres",
        "categorias_taller",
        ["categoria_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        sa.text(
            "UPDATE talleres "
            "SET categoria_id = (SELECT id FROM categorias_taller WHERE slug = :slug) "
            "WHERE categoria_id IS NULL"
        ).bindparams(slug="general")
    )
    op.alter_column("talleres", "categoria_id", existing_type=sa.Integer(), nullable=False)

    op.add_column("talleres", sa.Column("horarios", sa.Text(), nullable=True))
    op.add_column("talleres", sa.Column("certificaciones", sa.Text(), nullable=True))
    op.add_column(
        "talleres",
        sa.Column(
            "tarifas_base",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("talleres", sa.Column("rating_promedio", sa.Float(), nullable=False, server_default="0"))
    op.add_column("talleres", sa.Column("rating_total", sa.Integer(), nullable=False, server_default="0"))

    op.add_column("solicitudes", sa.Column("fecha_incidente", sa.DateTime(timezone=True), nullable=True))
    op.add_column("solicitudes", sa.Column("danos_descripcion", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("ubicacion_texto", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("categoria_dano", sa.Text(), nullable=True))
    op.add_column("solicitudes", sa.Column("presupuesto_aceptado", sa.Float(), nullable=True))
    op.add_column("solicitudes", sa.Column("ruta_osrm", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("solicitudes", sa.Column("ruta_distancia_km", sa.Float(), nullable=True))
    op.add_column("solicitudes", sa.Column("ruta_eta_min", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_solicitudes_taller_id"), "solicitudes", ["taller_id"], unique=False)
    op.create_index(op.f("ix_solicitudes_tecnico_id"), "solicitudes", ["tecnico_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_solicitudes_tecnico_id"), table_name="solicitudes")
    op.drop_index(op.f("ix_solicitudes_taller_id"), table_name="solicitudes")
    op.drop_column("solicitudes", "ruta_eta_min")
    op.drop_column("solicitudes", "ruta_distancia_km")
    op.drop_column("solicitudes", "ruta_osrm")
    op.drop_column("solicitudes", "presupuesto_aceptado")
    op.drop_column("solicitudes", "categoria_dano")
    op.drop_column("solicitudes", "ubicacion_texto")
    op.drop_column("solicitudes", "danos_descripcion")
    op.drop_column("solicitudes", "fecha_incidente")

    op.drop_column("talleres", "rating_total")
    op.drop_column("talleres", "rating_promedio")
    op.drop_column("talleres", "tarifas_base")
    op.drop_column("talleres", "certificaciones")
    op.drop_column("talleres", "horarios")
    op.drop_constraint("fk_talleres_categoria_id_categorias_taller", "talleres", type_="foreignkey")
    op.drop_index(op.f("ix_talleres_categoria_id"), table_name="talleres")
    op.drop_column("talleres", "categoria_id")

    op.drop_index(op.f("ix_categorias_taller_slug"), table_name="categorias_taller")
    op.drop_index(op.f("ix_categorias_taller_id"), table_name="categorias_taller")
    op.drop_table("categorias_taller")

