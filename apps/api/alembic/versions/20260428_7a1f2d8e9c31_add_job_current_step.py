"""add_job_current_step

Revision ID: 7a1f2d8e9c31
Revises: ddb77358d774
Create Date: 2026-04-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7a1f2d8e9c31"
down_revision: Union[str, None] = "ddb77358d774"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("current_step", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "current_step")
