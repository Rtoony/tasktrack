"""Add needs_review to internal follow-up items.

Revision ID: 3f8a72b6c901
Revises: 9b7c3a1d5e4f
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3f8a72b6c901"
down_revision: Union[str, Sequence[str], None] = "9b7c3a1d5e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("personal_items") as batch_op:
        batch_op.add_column(sa.Column("needs_review", sa.Integer(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    with op.batch_alter_table("personal_items") as batch_op:
        batch_op.drop_column("needs_review")
