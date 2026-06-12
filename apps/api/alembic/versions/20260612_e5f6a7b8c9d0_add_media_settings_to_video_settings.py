"""add_media_settings_to_video_generation_settings

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Project-level background music + subtitle styling, stored as JSON.
    # Nullable and additive: existing rows default to None → previous behavior.
    op.add_column(
        "video_generation_settings",
        sa.Column("media_settings", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("video_generation_settings", "media_settings")
