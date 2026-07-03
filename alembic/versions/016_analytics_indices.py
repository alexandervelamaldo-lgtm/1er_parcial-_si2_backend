"""create composite indices to speed up analytics dashboard queries

Las queries del dashboard /analytics filtran masivamente por:
  - rango de fechas (fecha_solicitud BETWEEN ... AND ...)
  - taller_id (cuando el admin filtra por taller)
  - estado_id (excluyendo CANCELADAS/RECHAZADAS_TALLER)

Los índices del schema inicial cubren `estado_id` y `taller_id` por
separado, pero no como composite con `fecha_solicitud`. Con datasets
de ~50k filas y un rango de 30 días, el planner termina haciendo
bitmap scan + heap. Estos composite hacen que las queries usen
index-only scan donde aplica.

Tambén indexamos `fecha_asignacion` (no estaba) — lo usamos para
calcular K1 (avg tiempo de asignación) y K6 (ranking eficiencia).

NOTA: estos índices son seguros — solo aceleran lecturas. Si por
alguna razón quieres revertir, el `downgrade()` los borra sin
afectar datos.

Revision ID: 016_analytics_indices
Revises: 015_ia_audit_log
Create Date: 2026-05-30
"""

from collections.abc import Sequence

from alembic import op


revision: str = "016_analytics_indices"
down_revision: str | Sequence[str] | None = "015_ia_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Rango de fechas — usado por TODAS las queries del dashboard.
    op.create_index(
        "ix_solicitudes_fecha_solicitud",
        "solicitudes",
        ["fecha_solicitud"],
        if_not_exists=True,
    )
    # Para K1 (tiempo de asignación) y K6 (ranking) donde filtramos
    # por `fecha_asignacion IS NOT NULL` y hacemos AVG.
    op.create_index(
        "ix_solicitudes_fecha_asignacion",
        "solicitudes",
        ["fecha_asignacion"],
        if_not_exists=True,
    )
    # Composite: filtramos por estado + rango de fechas en K8 y K9.
    op.create_index(
        "ix_solicitudes_estado_fecha",
        "solicitudes",
        ["estado_id", "fecha_solicitud"],
        if_not_exists=True,
    )
    # Composite: dashboard de un taller específico (filtra por taller_id
    # + rango). Ya hay índice simple en taller_id pero el composite es
    # mejor con el rango de fechas.
    op.create_index(
        "ix_solicitudes_taller_fecha",
        "solicitudes",
        ["taller_id", "fecha_solicitud"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_solicitudes_taller_fecha", table_name="solicitudes")
    op.drop_index("ix_solicitudes_estado_fecha", table_name="solicitudes")
    op.drop_index("ix_solicitudes_fecha_asignacion", table_name="solicitudes")
    op.drop_index("ix_solicitudes_fecha_solicitud", table_name="solicitudes")
