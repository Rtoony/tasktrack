"""project overlays

Revision ID: 6a5d4c3b2a10
Revises: 19f4c8b2a6d0
Create Date: 2026-05-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6a5d4c3b2a10"
down_revision: Union[str, Sequence[str], None] = "19f4c8b2a6d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_overlays",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("project_number", sa.Text(), nullable=False),
        sa.Column("operator_status", sa.Text(), server_default=sa.text("''")),
        sa.Column("priority", sa.Text(), server_default=sa.text("''")),
        sa.Column("tags", sa.Text(), server_default=sa.text("''")),
        sa.Column("next_review_date", sa.Text(), server_default=sa.text("''")),
        sa.Column("internal_notes", sa.Text(), server_default=sa.text("''")),
        sa.Column("report_note", sa.Text(), server_default=sa.text("''")),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_project_overlays_project_id", "project_overlays", ["project_id"], unique=True)
    op.create_index("idx_project_overlays_project_number", "project_overlays", ["project_number"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_project_overlays_project_number", table_name="project_overlays")
    op.drop_index("idx_project_overlays_project_id", table_name="project_overlays")
    op.drop_table("project_overlays")
