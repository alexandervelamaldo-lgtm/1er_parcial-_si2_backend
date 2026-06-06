"""add PROPUESTA_TALLER and RECHAZADA_TALLER request states

Introduces the two intermediate states required by the new
client↔workshop-direct flow:

  - REGISTRADA → PROPUESTA_TALLER   (cliente eligió taller, espera aceptación)
  - PROPUESTA_TALLER → RECHAZADA_TALLER (taller rechazó)
  - RECHAZADA_TALLER → PROPUESTA_TALLER (cliente vuelve a elegir otro)
  - PROPUESTA_TALLER → ASIGNADA      (taller aceptó)

We also persist a per-Solicitud counter of consecutive workshop rejections
so the backend can escalate to operadores when it reaches 3.

Revision ID: 014_propuesta_rechazada
Revises: 013_taller_marca_asociada
Create Date: 2026-05-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "014_propuesta_rechazada"
down_revision: str | Sequence[str] | None = "013_taller_marca_asociada"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Estados nuevos en orden — ON CONFLICT garantiza idempotencia entre tenants.
_NEW_ESTADOS = ("PROPUESTA_TALLER", "RECHAZADA_TALLER")


def upgrade() -> None:
    # 1. Insertar los nuevos estados en el catálogo. Cada tenant tiene su
    #    propia DB pero las migraciones se corren por tenant — ON CONFLICT
    #    evita duplicados si ya estaban sembrados por _seed_catalogs.
    for nombre in _NEW_ESTADOS:
        op.execute(
            sa.text(
                "INSERT INTO estados_solicitud (nombre) VALUES (:n) "
                "ON CONFLICT (nombre) DO NOTHING"
            ).bindparams(n=nombre)
        )

    # 2. Contador de rechazos consecutivos del taller para esta solicitud.
    #    Lo bumpea el endpoint de respuesta-taller y se resetea al volver
    #    a REGISTRADA o al aceptarse. Default 0 para filas existentes.
    op.add_column(
        "solicitudes",
        sa.Column(
            "taller_rechazos_consecutivos",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("solicitudes", "taller_rechazos_consecutivos")
    for nombre in _NEW_ESTADOS:
        op.execute(
            sa.text("DELETE FROM estados_solicitud WHERE nombre = :n").bindparams(
                n=nombre
            )
        )
