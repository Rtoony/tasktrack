"""comments.audience — address a comment to the AI developer.

Revision ID: f4a1c7d9e2b6
Revises: c7e2f5a1d4b9
Create Date: 2026-06-13

Feedback #42: let an operator leave AI-directed dev instructions inside the
existing per-record comment thread. A new `audience` column tags a comment
as '' (a normal human comment, the default) or 'ai-dev' (addressed to the
headless co-developer). Additive, server_default '' so every existing
comment is a normal comment — no backfill needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a1c7d9e2b6"
down_revision: Union[str, Sequence[str], None] = "c7e2f5a1d4b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("comments") as batch_op:
        batch_op.add_column(
            sa.Column("audience", sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("comments") as batch_op:
        batch_op.drop_column("audience")
