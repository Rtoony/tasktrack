"""report presets

Revision ID: 9b7c3a1d5e4f
Revises: 6a5d4c3b2a10
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b7c3a1d5e4f"
down_revision: Union[str, Sequence[str], None] = "6a5d4c3b2a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_presets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("filters_json", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("is_shared", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_report_presets_surface", "report_presets", ["surface"])
    op.create_index("idx_report_presets_owner", "report_presets", ["owner_user_id"])
    op.create_index("idx_report_presets_shared", "report_presets", ["is_shared"])


def downgrade() -> None:
    op.drop_index("idx_report_presets_shared", table_name="report_presets")
    op.drop_index("idx_report_presets_owner", table_name="report_presets")
    op.drop_index("idx_report_presets_surface", table_name="report_presets")
    op.drop_table("report_presets")
