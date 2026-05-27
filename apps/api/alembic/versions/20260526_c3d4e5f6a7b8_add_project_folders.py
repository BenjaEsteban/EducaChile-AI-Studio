"""add_project_folders

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine.reflection import Inspector

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("folders"):
        op.create_table(
            "folders",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "organization_id",
                UUID(as_uuid=True),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "parent_folder_id",
                UUID(as_uuid=True),
                sa.ForeignKey("folders.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        inspector = sa.inspect(bind)

    if not _index_exists(inspector, "folders", "ix_folders_organization_id"):
        op.create_index("ix_folders_organization_id", "folders", ["organization_id"], unique=False)
    if not _index_exists(inspector, "folders", "ix_folders_parent_folder_id"):
        op.create_index("ix_folders_parent_folder_id", "folders", ["parent_folder_id"], unique=False)

    if not _column_exists(inspector, "projects", "folder_id"):
        op.add_column("projects", sa.Column("folder_id", UUID(as_uuid=True), nullable=True))
        inspector = sa.inspect(bind)
    if not _index_exists(inspector, "projects", "ix_projects_folder_id"):
        op.create_index("ix_projects_folder_id", "projects", ["folder_id"], unique=False)
    if not _foreign_key_exists(inspector, "projects", "fk_projects_folder_id_folders"):
        op.create_foreign_key(
            "fk_projects_folder_id_folders",
            "projects",
            "folders",
            ["folder_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _foreign_key_exists(inspector, "projects", "fk_projects_folder_id_folders"):
        op.drop_constraint("fk_projects_folder_id_folders", "projects", type_="foreignkey")
    if _index_exists(inspector, "projects", "ix_projects_folder_id"):
        op.drop_index("ix_projects_folder_id", table_name="projects")
    if _column_exists(inspector, "projects", "folder_id"):
        op.drop_column("projects", "folder_id")
    inspector = sa.inspect(bind)

    if inspector.has_table("folders"):
        if _index_exists(inspector, "folders", "ix_folders_parent_folder_id"):
            op.drop_index("ix_folders_parent_folder_id", table_name="folders")
        if _index_exists(inspector, "folders", "ix_folders_organization_id"):
            op.drop_index("ix_folders_organization_id", table_name="folders")
        op.drop_table("folders")


def _column_exists(inspector: Inspector, table_name: str, column_name: str) -> bool:
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: Inspector, table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _foreign_key_exists(inspector: Inspector, table_name: str, fk_name: str) -> bool:
    return any(fk.get("name") == fk_name for fk in inspector.get_foreign_keys(table_name))
