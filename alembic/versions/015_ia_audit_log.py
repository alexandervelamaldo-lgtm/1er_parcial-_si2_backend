"""create ia_audit_log table for OpenAI/Gemini call telemetry

La migración crea la tabla `ia_audit_log` para auditar cada llamada a la
IA (visión, audio, costo) con métricas de uso (tokens, latencia, costo
estimado en USD, confianza, fallback). Sirve para:

  - calibrar la calidad de las estimaciones contra facturas reales,
  - alertar si el provider está caído (muchos fallback=True seguidos),
  - controlar costos en producción.

NOTA: las columnas de costo en `solicitudes` (`costo_estimado`,
`costo_estimado_min`, `costo_estimado_max`, `costo_estimacion_confianza`,
`costo_estimacion_nota`, `moneda_costo`, `requiere_revision_manual`) ya
existen desde la migración 004 — esta migración NO las toca, solo agrega
la tabla de auditoría nueva.

Revision ID: 015_ia_audit_log
Revises: 014_propuesta_rechazada
Create Date: 2026-05-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "015_ia_audit_log"
down_revision: str | Sequence[str] | None = "014_propuesta_rechazada"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ia_audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "solicitud_id",
            sa.Integer(),
            sa.ForeignKey("solicitudes.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        # "vision" | "audio" | "costo" — tipo de llamada para filtrar costos.
        sa.Column("tipo", sa.String(length=24), nullable=False, index=True),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        # Costo en USD (no BOB) — métrica de gasto del provider, no del
        # cliente. Float es suficiente; 6 decimales bastan para gpt-4o-mini.
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        # Confianza del modelo en su respuesta (0-1).
        sa.Column("confianza", sa.Float(), nullable=True),
        # True si caímos al fallback degradado (la IA no respondió OK).
        sa.Column(
            "fallback",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Hash truncado del payload de entrada (no el payload completo —
        # ahorra espacio y respeta privacidad).
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Índice compuesto para reportes "gasto últimos 7 días por tipo".
    op.create_index(
        "ix_ia_audit_log_tipo_created",
        "ia_audit_log",
        ["tipo", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ia_audit_log_tipo_created", table_name="ia_audit_log")
    op.drop_table("ia_audit_log")
