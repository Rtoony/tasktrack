"""fix project_work_tasks.project_number nullable drift

Revision ID: d1f0b2a36c11
Revises: c4f1a8e2d917
Create Date: 2026-05-19

The 594357560fc3 migration added project_number to four tables. Three
of them landed with nullable=False as intended; project_work_tasks
landed with nullable=True due to a batch_alter_table quirk. The ORM
model declares `Mapped[str]` (not Optional[str]) so future inserts
that bypass server_default would violate the type contract.

This migration normalizes project_work_tasks.project_number to
NOT NULL DEFAULT '' to match the siblings.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1f0b2a36c11'
down_revision: Union[str, Sequence[str], None] = 'c4f1a8e2d917'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill any NULL values first (none expected; table is empty in
    # the live DB, but cheap insurance).
    op.execute(
        "UPDATE project_work_tasks SET project_number = '' "
        "WHERE project_number IS NULL"
    )
    with op.batch_alter_table('project_work_tasks', schema=None) as batch:
        batch.alter_column(
            'project_number',
            existing_type=sa.Text(),
            nullable=False,
            existing_server_default=sa.text("('')"),
        )


def downgrade() -> None:
    with op.batch_alter_table('project_work_tasks', schema=None) as batch:
        batch.alter_column(
            'project_number',
            existing_type=sa.Text(),
            nullable=True,
            existing_server_default=sa.text("('')"),
        )
