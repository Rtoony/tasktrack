"""Add feedback_items.dev_status — the co-dev pipeline's own build-state lane.

Separate from the human `status` enum (feedback #76): the pipeline gets a
column it may write (unclaimed → planned → building → tests-pass-awaiting-promote
→ promoted → abandoned) without ever overloading Josh's triage states. This is
the structural fix for the #68/#69 buried-fix incident — done-but-unpromoted
work becomes visible in the feedback record itself.

Revision ID: d7f3a9c20702
Revises: c1e8a2f3b9d4
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7f3a9c20702"
down_revision: Union[str, Sequence[str], None] = "c1e8a2f3b9d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "feedback_items",
        sa.Column("dev_status", sa.Text(), nullable=False,
                  server_default=sa.text("'unclaimed'")),
    )


def downgrade() -> None:
    op.drop_column("feedback_items", "dev_status")
