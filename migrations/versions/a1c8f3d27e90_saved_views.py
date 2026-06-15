"""saved views (per-user tracker filter/sort configurations)

Revision ID: a1c8f3d27e90
Revises: d8f1c4a7e620
Create Date: 2026-06-15

Additive only: creates the `saved_views` table backing P1-5. No existing
table is touched, so this is safe to roll forward and back independently.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c8f3d27e90"
down_revision: Union[str, Sequence[str], None] = "d8f1c4a7e620"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tab", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_saved_views_owner_tab", "saved_views", ["owner_user_id", "tab"])


def downgrade() -> None:
    op.drop_index("idx_saved_views_owner_tab", table_name="saved_views")
    op.drop_table("saved_views")
