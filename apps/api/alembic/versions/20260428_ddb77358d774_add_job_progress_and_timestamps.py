"""add_job_progress_and_timestamps

Revision ID: ddb77358d774
Revises: e894de060c97
Create Date: 2026-04-28

Agrega columnas de ciclo de vida al modelo Job:
  - progress (FLOAT, default 0.0)
  - started_at (TIMESTAMPTZ, nullable)
  - finished_at (TIMESTAMPTZ, nullable)

Renombra el status 'success' → 'completed' para mayor claridad semántica.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ddb77358d774"
down_revision: Union[str, None] = "e894de060c97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("progress", sa.Float, nullable=False, server_default="0.0"))
    op.add_column("jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    # Renombrar status success → completed en datos existentes
    op.execute("UPDATE jobs SET status = 'completed' WHERE status = 'success'")


def downgrade() -> None:
    op.execute("UPDATE jobs SET status = 'success' WHERE status = 'completed'")

    op.drop_column("jobs", "finished_at")
    op.drop_column("jobs", "started_at")
    op.drop_column("jobs", "progress")
