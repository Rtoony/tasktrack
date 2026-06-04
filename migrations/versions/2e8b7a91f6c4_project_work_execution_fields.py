"""project work execution fields

Revision ID: 2e8b7a91f6c4
Revises: d7c4e9b1a206
Create Date: 2026-06-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2e8b7a91f6c4"
down_revision: Union[str, Sequence[str], None] = "d7c4e9b1a206"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project_work_tasks") as batch:
        batch.add_column(sa.Column("scheduled_completion_at", sa.Text(), nullable=True, server_default=sa.text("''")))
        batch.add_column(sa.Column("time_required_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch.add_column(sa.Column("scope_notes", sa.Text(), nullable=True, server_default=sa.text("''")))
        batch.add_column(sa.Column("progress_notes", sa.Text(), nullable=True, server_default=sa.text("''")))
        batch.add_column(sa.Column("confirmation_notes", sa.Text(), nullable=True, server_default=sa.text("''")))
        batch.add_column(sa.Column("completion_notes", sa.Text(), nullable=True, server_default=sa.text("''")))
    op.create_index(
        "idx_project_work_tasks_scheduled_completion",
        "project_work_tasks",
        ["scheduled_completion_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_project_work_tasks_scheduled_completion", table_name="project_work_tasks")
    with op.batch_alter_table("project_work_tasks") as batch:
        batch.drop_column("completion_notes")
        batch.drop_column("confirmation_notes")
        batch.drop_column("progress_notes")
        batch.drop_column("scope_notes")
        batch.drop_column("time_required_minutes")
        batch.drop_column("scheduled_completion_at")
