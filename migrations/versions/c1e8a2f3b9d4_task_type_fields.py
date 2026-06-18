"""Relevance Type fields on Project / Incident / Training forms (feedback #46).

Revision ID: c1e8a2f3b9d4
Revises: b3d7f1a5c802
Create Date: 2026-06-18

#46 form revamp: mirror the CAD Dev Type field across the other task forms so work
self-classifies at capture (low-friction filtering). Adds nullable TEXT columns:
  - project_work_tasks.task_type   (Design / Review / Submittal / Field / Admin / Other)
  - personnel_issues.coaching_type (Coaching / Near-miss / Recognition / Process gap) — the
    non-punitive lever: the incident form can now log wins, not just problems
  - training_tasks.training_type   (Skill Coaching / Onboarding / Software Rollout / ...)
Additive, non-destructive, server_default '' so existing rows are untouched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1e8a2f3b9d4"
down_revision: Union[str, Sequence[str], None] = "b3d7f1a5c802"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADDS = [
    ("project_work_tasks", "task_type"),
    ("personnel_issues", "coaching_type"),
    ("training_tasks", "training_type"),
]


def upgrade() -> None:
    for table, col in _ADDS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column(col, sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    for table, col in _ADDS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column(col)
