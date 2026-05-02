"""add_video_generation_settings

Revision ID: a7b8c9d0e1f2
Revises: 9c1f0a2b3d4e
Create Date: 2026-05-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "9c1f0a2b3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("current_slide", sa.Integer(), nullable=True))
    op.add_column("generation_jobs", sa.Column("total_slides", sa.Integer(), nullable=True))

    op.create_table(
        "video_generation_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("elevenlabs_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("elevenlabs_api_key_last_four", sa.String(8), nullable=True),
        sa.Column("elevenlabs_voice_id", sa.String(255), nullable=True),
        sa.Column("wavespeed_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("wavespeed_api_key_last_four", sa.String(8), nullable=True),
        sa.Column("validation_status", sa.String(50), nullable=False, server_default="not_configured"),
        sa.Column("elevenlabs_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("wavespeed_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_video_generation_settings_project_id"),
    )
    op.create_index(
        "ix_video_generation_settings_organization_id",
        "video_generation_settings",
        ["organization_id"],
    )
    op.create_index(
        "ix_video_generation_settings_project_id",
        "video_generation_settings",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_generation_settings_project_id", table_name="video_generation_settings")
    op.drop_index(
        "ix_video_generation_settings_organization_id",
        table_name="video_generation_settings",
    )
    op.drop_table("video_generation_settings")
    op.drop_column("generation_jobs", "total_slides")
    op.drop_column("generation_jobs", "current_slide")
