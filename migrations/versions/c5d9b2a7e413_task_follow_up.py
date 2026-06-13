"""follow_up flag on the core trackers (feedback #44).

Revision ID: c5d9b2a7e413
Revises: a3b8e1f64c92
Create Date: 2026-06-13

Feedback #44: let any task be flagged as a "follow-up" — a lightweight star,
separate from the calendar due-date system. Adds a 0/1 `follow_up` flag
(default 0) to every core tracker. Additive and non-destructive; no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d9b2a7e413"
down_revision: Union[str, Sequence[str], None] = "a3b8e1f64c92"
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
            batch_op.add_column(sa.Column("follow_up", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("follow_up")
