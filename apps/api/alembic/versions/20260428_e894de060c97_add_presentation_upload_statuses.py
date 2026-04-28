"""add_presentation_upload_statuses

Revision ID: e894de060c97
Revises: e0c382f59d51
Create Date: 2026-04-28

Agrega los valores 'upload_pending' y 'uploaded' al ciclo de vida de Presentation.
Como el campo status es VARCHAR(50) (no un tipo ENUM de PostgreSQL), no se requiere
DDL adicional — los nuevos valores son válidos en cuanto la aplicación los use.

Nuevo ciclo de vida:
  upload_pending → uploaded → processing → ready
                                         ↘ failed
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e894de060c97"
down_revision: Union[str, None] = "e0c382f59d51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Actualizar filas existentes con status 'pending' al nuevo nombre 'upload_pending'
    # para mantener consistencia con datos previos de desarrollo.
    op.execute(
        "UPDATE presentations SET status = 'upload_pending' WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE presentations SET status = 'pending' WHERE status = 'upload_pending'"
    )
    op.execute(
        "UPDATE presentations SET status = 'pending' WHERE status = 'uploaded'"
    )
