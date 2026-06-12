"""Add advisory AI suggestion columns to inbox_items.

Revision ID: b9e4a7c3d2f8
Revises: 6f0b3c2d4e5a
Create Date: 2026-06-12

Triage+Assignment unification (W2): the inbox is the dump ground, the
SYSTEM suggests a target tracker + drafted fields, and the HUMAN has
final say at promote ("Assignment") time. All three columns are
nullable — NULL means "no suggestion yet". suggestion_json stores the
full advisory suggestion dict; suggested_table is denormalized from it
for cheap filtering; suggested_at records when the suggestion landed.
Suggestions never auto-create tracker rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9e4a7c3d2f8"
down_revision: Union[str, Sequence[str], None] = "6f0b3c2d4e5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("inbox_items") as batch_op:
        batch_op.add_column(sa.Column("suggested_table", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("suggestion_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("suggested_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inbox_items") as batch_op:
        batch_op.drop_column("suggested_at")
        batch_op.drop_column("suggestion_json")
        batch_op.drop_column("suggested_table")
