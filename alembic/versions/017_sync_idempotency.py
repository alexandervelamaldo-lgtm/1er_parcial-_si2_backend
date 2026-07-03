"""persistent idempotency table for offline batch-sync (/sync/lote)

El endpoint `/sync/lote` reproduce operaciones que el móvil encoló estando
offline. Cada operación trae un `idempotency_key` (UUID) para que un reintento
del cliente no cree duplicados. Hasta ahora ese registro vivía en una caché en
memoria por proceso (`_IDEM_CACHE`), que:

  - se pierde en cada reinicio / cold-start (Render apaga el contenedor),
  - no se comparte entre réplicas/instancias.

En ambos casos un reintento legítimo del cliente re-ejecutaría la operación →
solicitud duplicada. Esta tabla persiste la clave + el resultado original para
que el dedup funcione cross-process y sobreviva reinicios.

`resultado` (Text/JSON) permite devolver la MISMA respuesta ante un duplicado
sin volver a ejecutar el handler. El índice en `creado_en` soporta la purga de
retención (~7 días) sin escaneo completo.

Revision ID: 017_sync_idempotency
Revises: 016_analytics_indices
Create Date: 2026-06-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "017_sync_idempotency"
down_revision: str | Sequence[str] | None = "016_analytics_indices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_idempotencia",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("tipo", sa.String(length=40), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("resultado", sa.Text(), nullable=False),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Un índice UNIQUE enforcea la unicidad de la clave (en Postgres y SQLite)
    # y a la vez acelera la búsqueda por clave, que es la ruta caliente: cada
    # operación del lote consulta primero si su idempotency_key ya existe.
    op.create_index(
        "ix_sync_idempotencia_key",
        "sync_idempotencia",
        ["idempotency_key"],
        unique=True,
        if_not_exists=True,
    )
    # Para la purga de retención por fecha.
    op.create_index(
        "ix_sync_idempotencia_creado_en",
        "sync_idempotencia",
        ["creado_en"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_sync_idempotencia_creado_en", table_name="sync_idempotencia")
    op.drop_index("ix_sync_idempotencia_key", table_name="sync_idempotencia")
    op.drop_table("sync_idempotencia")
