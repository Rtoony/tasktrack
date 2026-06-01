"""feedback loop metadata

Revision ID: d7c4e9b1a206
Revises: cf37b1a2d904
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7c4e9b1a206"
down_revision: Union[str, Sequence[str], None] = "cf37b1a2d904"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feedback_items") as batch:
        batch.add_column(sa.Column("resolution_metadata_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")))
    op.execute("UPDATE feedback_items SET completed_at = NULL WHERE status = 'Fixed'")


def downgrade() -> None:
    with op.batch_alter_table("feedback_items") as batch:
        batch.drop_column("resolution_metadata_json")
