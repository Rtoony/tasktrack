"""employee photos

Revision ID: cf37b1a2d904
Revises: a9d4e2f6c8b1
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cf37b1a2d904"
down_revision: Union[str, Sequence[str], None] = "a9d4e2f6c8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.add_column(sa.Column("photo_path", sa.Text(), nullable=False, server_default=sa.text("''")))
        batch.add_column(sa.Column("photo_source_url", sa.Text(), nullable=False, server_default=sa.text("''")))
        batch.add_column(sa.Column("photo_updated_at", sa.Text(), nullable=False, server_default=sa.text("''")))


def downgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.drop_column("photo_updated_at")
        batch.drop_column("photo_source_url")
        batch.drop_column("photo_path")
