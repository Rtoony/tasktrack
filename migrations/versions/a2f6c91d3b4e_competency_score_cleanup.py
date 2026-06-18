"""Competency score cleanup + trajectory (feedback #51 Wave 2).

Revision ID: a2f6c91d3b4e
Revises: c8e1f4a09b2d
Create Date: 2026-06-17

#51 Wave 2: (1) drop the legacy `score DEFAULT 5.0` on employee_skill_scores — a
1-10-era artifact that is misleading now that scores live on the 0-3 ladder (the
rollup always sets a real value; the default only ever applied to never-rolled
rows). Normalize to 0.0. (2) Add a nullable `trajectory` column (Text:
'rising'/'steady'/NULL) for the Capability Snapshot's potential/direction flag,
consumed by Wave 3. Additive + non-destructive; data is near-empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2f6c91d3b4e"
down_revision: Union[str, Sequence[str], None] = "c8e1f4a09b2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("employee_skill_scores") as batch_op:
        batch_op.alter_column("score", server_default="0.0")
        batch_op.add_column(sa.Column("trajectory", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("employee_skill_scores") as batch_op:
        batch_op.drop_column("trajectory")
        batch_op.alter_column("score", server_default="5.0")
