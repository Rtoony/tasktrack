"""CAD Dev task Type field (feedback #46).

Revision ID: b3d7f1a5c802
Revises: a2f6c91d3b4e
Create Date: 2026-06-18

#46: CAD Dev tasks are clearly Idea / Bug / Feature / Improvement (their titles +
intake "Request type:" prove it), but there was no field for it. Adds a nullable
`task_type` Text column to work_tasks. Additive, non-destructive. (The form also
drops CAD Skill Area + Software per Josh — those columns stay, just hidden.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3d7f1a5c802"
down_revision: Union[str, Sequence[str], None] = "a2f6c91d3b4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("work_tasks") as batch_op:
        batch_op.add_column(sa.Column("task_type", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("work_tasks") as batch_op:
        batch_op.drop_column("task_type")
