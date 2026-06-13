"""archived_at on the core trackers — Archive (vs hard Delete).

Revision ID: a3b8e1f64c92
Revises: f4a1c7d9e2b6
Create Date: 2026-06-13

Feedback #38: the main-view "Delete" becomes "Archive" (soft, reversible,
retained for long-term analysis); hard Delete moves inside the record drawer.
Adds a nullable `archived_at` timestamp to every core tracker — NULL = active
(the default for every existing row), a timestamp = archived. Additive and
non-destructive; no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b8e1f64c92"
down_revision: Union[str, Sequence[str], None] = "f4a1c7d9e2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "work_tasks",
    "project_work_tasks",
    "training_tasks",
    "personnel_issues",
    "calendar_events",
    "personal_items",
)


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("archived_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("archived_at")
