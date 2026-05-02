"""add_generation_pipeline_tables

Revision ID: 9c1f0a2b3d4e
Revises: 4b7c3a0d2f91
Create Date: 2026-05-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "9c1f0a2b3d4e"
down_revision: Union[str, None] = "4b7c3a0d2f91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project_generation_configs",
        sa.Column("ai_provider", sa.String(50), nullable=False, server_default="gemini"),
    )
    op.add_column("project_generation_configs", sa.Column("avatar_id", sa.String(255), nullable=True))
    op.add_column(
        "project_generation_configs",
        sa.Column("language", sa.String(20), nullable=False, server_default="es"),
    )
    op.add_column(
        "project_generation_configs",
        sa.Column("output_format", sa.String(20), nullable=False, server_default="mp4"),
    )
    op.add_column(
        "project_generation_configs",
        sa.Column("subtitles_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "project_generation_configs",
        sa.Column(
            "background_music_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "project_generation_configs",
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
    )

    op.create_table(
        "provider_credentials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_name", sa.String(50), nullable=False),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("encrypted_api_key", sa.Text, nullable=False),
        sa.Column("key_last_four", sa.String(8), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="configured"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "provider_name",
            "provider_type",
            name="uq_provider_credentials_org_provider_type",
        ),
    )
    op.create_index("ix_provider_credentials_organization_id", "provider_credentials", ["organization_id"])
    op.create_index("ix_provider_credentials_provider_name", "provider_credentials", ["provider_name"])
    op.create_index("ix_provider_credentials_provider_type", "provider_credentials", ["provider_type"])
    op.create_index("ix_provider_credentials_status", "provider_credentials", ["status"])

    op.add_column("assets", sa.Column("slide_id", UUID(as_uuid=True), nullable=True))
    op.add_column("assets", sa.Column("duration_seconds", sa.Float(), nullable=True))
    op.add_column("assets", sa.Column("metadata_json", JSONB(), nullable=True))
    op.create_foreign_key(
        "fk_assets_slide_id_slides",
        "assets",
        "slides",
        ["slide_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_assets_slide_id", "assets", ["slide_id"])

    op.create_table(
        "generation_jobs",
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
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("progress_percentage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("current_step", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "final_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_generation_jobs_organization_id", "generation_jobs", ["organization_id"])
    op.create_index("ix_generation_jobs_project_id", "generation_jobs", ["project_id"])
    op.create_index("ix_generation_jobs_job_id", "generation_jobs", ["job_id"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_job_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_project_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_organization_id", table_name="generation_jobs")
    op.drop_table("generation_jobs")

    op.drop_index("ix_assets_slide_id", table_name="assets")
    op.drop_constraint("fk_assets_slide_id_slides", "assets", type_="foreignkey")
    op.drop_column("assets", "metadata_json")
    op.drop_column("assets", "duration_seconds")
    op.drop_column("assets", "slide_id")

    op.drop_index("ix_provider_credentials_status", table_name="provider_credentials")
    op.drop_index("ix_provider_credentials_provider_type", table_name="provider_credentials")
    op.drop_index("ix_provider_credentials_provider_name", table_name="provider_credentials")
    op.drop_index("ix_provider_credentials_organization_id", table_name="provider_credentials")
    op.drop_table("provider_credentials")
    op.drop_column("project_generation_configs", "status")
    op.drop_column("project_generation_configs", "background_music_enabled")
    op.drop_column("project_generation_configs", "subtitles_enabled")
    op.drop_column("project_generation_configs", "output_format")
    op.drop_column("project_generation_configs", "language")
    op.drop_column("project_generation_configs", "avatar_id")
    op.drop_column("project_generation_configs", "ai_provider")
