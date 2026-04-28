"""add_project_generation_configs

Revision ID: 4b7c3a0d2f91
Revises: 7a1f2d8e9c31
Create Date: 2026-04-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "4b7c3a0d2f91"
down_revision: Union[str, None] = "7a1f2d8e9c31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_generation_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tts_provider", sa.String(50), nullable=False, server_default="gemini"),
        sa.Column("video_provider", sa.String(50), nullable=False, server_default="wavespeed"),
        sa.Column("voice_id", sa.String(255), nullable=True),
        sa.Column("voice_name", sa.String(255), nullable=True),
        sa.Column("gemini_api_key_encrypted", sa.Text, nullable=True),
        sa.Column("elevenlabs_api_key_encrypted", sa.Text, nullable=True),
        sa.Column("wavespeed_api_key_encrypted", sa.Text, nullable=True),
        sa.Column("resolution", sa.String(20), nullable=False, server_default="1920x1080"),
        sa.Column("aspect_ratio", sa.String(20), nullable=False, server_default="16:9"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_project_generation_configs_organization_id",
        "project_generation_configs",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_generation_configs_organization_id",
        table_name="project_generation_configs",
    )
    op.drop_table("project_generation_configs")
