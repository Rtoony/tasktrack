"""AI task summary on Project + CAD Dev trackers (feedback #34).

Revision ID: c8e1f4a09b2d
Revises: a1c8f3d27e90
Create Date: 2026-06-17

Feedback #34: a local homelab model drafts a plain-language summary of a task
(suggest-and-confirm — the operator accepts/edits before it is stored). Adds a
nullable `ai_summary` Text column to the two richest trackers: work_tasks
(CAD Dev) and project_work_tasks (Project). Additive, non-destructive, no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e1f4a09b2d"
down_revision: Union[str, Sequence[str], None] = "a1c8f3d27e90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("work_tasks", "project_work_tasks")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("ai_summary", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("ai_summary")
