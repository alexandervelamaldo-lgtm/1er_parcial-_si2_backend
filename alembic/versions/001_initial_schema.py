"""initial schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-03-31 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


prioridad_enum = sa.Enum("BAJA", "MEDIA", "ALTA", "CRITICA", name="prioridad_solicitud", create_type=False)


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False),
    )
    op.create_index("ix_roles_id", "roles", ["id"])
    op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "clientes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("nombre", sa.String(length=150), nullable=False),
        sa.Column("telefono", sa.String(length=30), nullable=False),
        sa.Column("direccion", sa.String(length=255), nullable=False),
        sa.Column("latitud", sa.Float(), nullable=True),
        sa.Column("longitud", sa.Float(), nullable=True),
    )
    op.create_index("ix_clientes_id", "clientes", ["id"])

    op.create_table(
        "tecnicos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("nombre", sa.String(length=150), nullable=False),
        sa.Column("telefono", sa.String(length=30), nullable=False),
        sa.Column("especialidad", sa.String(length=120), nullable=False),
        sa.Column("latitud_actual", sa.Float(), nullable=True),
        sa.Column("longitud_actual", sa.Float(), nullable=True),
        sa.Column("disponibilidad", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_tecnicos_id", "tecnicos", ["id"])

    op.create_table(
        "operadores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("nombre", sa.String(length=150), nullable=False),
        sa.Column("turno", sa.String(length=60), nullable=False),
    )
    op.create_index("ix_operadores_id", "operadores", ["id"])

    op.create_table(
        "talleres",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=150), nullable=False),
        sa.Column("direccion", sa.String(length=255), nullable=False),
        sa.Column("latitud", sa.Float(), nullable=False),
        sa.Column("longitud", sa.Float(), nullable=False),
        sa.Column("telefono", sa.String(length=30), nullable=False),
        sa.Column("capacidad", sa.Integer(), nullable=False),
    )
    op.create_index("ix_talleres_id", "talleres", ["id"])

    op.create_table(
        "vehiculos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marca", sa.String(length=100), nullable=False),
        sa.Column("modelo", sa.String(length=100), nullable=False),
        sa.Column("anio", sa.Integer(), nullable=False),
        sa.Column("placa", sa.String(length=20), nullable=False),
        sa.Column("color", sa.String(length=50), nullable=False),
        sa.Column("tipo_combustible", sa.String(length=50), nullable=False),
    )
    op.create_index("ix_vehiculos_id", "vehiculos", ["id"])
    op.create_index("ix_vehiculos_cliente_id", "vehiculos", ["cliente_id"])
    op.create_index("ix_vehiculos_placa", "vehiculos", ["placa"], unique=True)

    op.create_table(
        "tipos_incidente",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
    )
    op.create_index("ix_tipos_incidente_id", "tipos_incidente", ["id"])
    op.create_index("ix_tipos_incidente_nombre", "tipos_incidente", ["nombre"], unique=True)

    op.create_table(
        "estados_solicitud",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=60), nullable=False),
    )
    op.create_index("ix_estados_solicitud_id", "estados_solicitud", ["id"])
    op.create_index("ix_estados_solicitud_nombre", "estados_solicitud", ["nombre"], unique=True)

    op.create_table(
        "solicitudes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehiculo_id", sa.Integer(), sa.ForeignKey("vehiculos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tecnico_id", sa.Integer(), sa.ForeignKey("tecnicos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("taller_id", sa.Integer(), sa.ForeignKey("talleres.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tipo_incidente_id", sa.Integer(), sa.ForeignKey("tipos_incidente.id"), nullable=False),
        sa.Column("estado_id", sa.Integer(), sa.ForeignKey("estados_solicitud.id"), nullable=False),
        sa.Column("latitud_incidente", sa.Float(), nullable=False),
        sa.Column("longitud_incidente", sa.Float(), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("foto_url", sa.Text(), nullable=True),
        sa.Column("prioridad", prioridad_enum, nullable=False),
        sa.Column("fecha_solicitud", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("fecha_asignacion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fecha_atencion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fecha_cierre", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_solicitudes_id", "solicitudes", ["id"])
    op.create_index("ix_solicitudes_cliente_id", "solicitudes", ["cliente_id"])
    op.create_index("ix_solicitudes_vehiculo_id", "solicitudes", ["vehiculo_id"])
    op.create_index("ix_solicitudes_tipo_incidente_id", "solicitudes", ["tipo_incidente_id"])
    op.create_index("ix_solicitudes_estado_id", "solicitudes", ["estado_id"])

    op.create_table(
        "notificaciones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("titulo", sa.String(length=150), nullable=False),
        sa.Column("mensaje", sa.Text(), nullable=False),
        sa.Column("tipo", sa.String(length=60), nullable=False),
        sa.Column("leida", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notificaciones_id", "notificaciones", ["id"])
    op.create_index("ix_notificaciones_usuario_id", "notificaciones", ["usuario_id"])

    op.create_table(
        "historial_eventos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("estado_anterior", sa.String(length=60), nullable=False),
        sa.Column("estado_nuevo", sa.String(length=60), nullable=False),
        sa.Column("observacion", sa.Text(), nullable=False),
        sa.Column("fecha_evento", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_historial_eventos_id", "historial_eventos", ["id"])
    op.create_index("ix_historial_eventos_solicitud_id", "historial_eventos", ["solicitud_id"])


def downgrade() -> None:
    op.drop_index("ix_historial_eventos_solicitud_id", table_name="historial_eventos")
    op.drop_index("ix_historial_eventos_id", table_name="historial_eventos")
    op.drop_table("historial_eventos")
    op.drop_index("ix_notificaciones_usuario_id", table_name="notificaciones")
    op.drop_index("ix_notificaciones_id", table_name="notificaciones")
    op.drop_table("notificaciones")
    op.drop_index("ix_solicitudes_estado_id", table_name="solicitudes")
    op.drop_index("ix_solicitudes_tipo_incidente_id", table_name="solicitudes")
    op.drop_index("ix_solicitudes_vehiculo_id", table_name="solicitudes")
    op.drop_index("ix_solicitudes_cliente_id", table_name="solicitudes")
    op.drop_index("ix_solicitudes_id", table_name="solicitudes")
    op.drop_table("solicitudes")
    op.drop_index("ix_estados_solicitud_nombre", table_name="estados_solicitud")
    op.drop_index("ix_estados_solicitud_id", table_name="estados_solicitud")
    op.drop_table("estados_solicitud")
    op.drop_index("ix_tipos_incidente_nombre", table_name="tipos_incidente")
    op.drop_index("ix_tipos_incidente_id", table_name="tipos_incidente")
    op.drop_table("tipos_incidente")
    op.drop_index("ix_vehiculos_placa", table_name="vehiculos")
    op.drop_index("ix_vehiculos_cliente_id", table_name="vehiculos")
    op.drop_index("ix_vehiculos_id", table_name="vehiculos")
    op.drop_table("vehiculos")
    op.drop_index("ix_talleres_id", table_name="talleres")
    op.drop_table("talleres")
    op.drop_index("ix_operadores_id", table_name="operadores")
    op.drop_table("operadores")
    op.drop_index("ix_tecnicos_id", table_name="tecnicos")
    op.drop_table("tecnicos")
    op.drop_index("ix_clientes_id", table_name="clientes")
    op.drop_table("clientes")
    op.drop_table("user_roles")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_roles_name", table_name="roles")
    op.drop_index("ix_roles_id", table_name="roles")
    op.drop_table("roles")
    prioridad_enum.drop(op.get_bind(), checkfirst=True)
