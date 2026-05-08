"""add_avatar_source_to_video_settings

Revision ID: b1c2d3e4f5a6
Revises: a7b8c9d0e1f2
Create Date: 2026-05-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "video_generation_settings",
        sa.Column("avatar_source_url", sa.String(1000), nullable=True),
    )
    op.add_column(
        "video_generation_settings",
        sa.Column("avatar_source_asset_id", UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("video_generation_settings", "avatar_source_asset_id")
    op.drop_column("video_generation_settings", "avatar_source_url")
