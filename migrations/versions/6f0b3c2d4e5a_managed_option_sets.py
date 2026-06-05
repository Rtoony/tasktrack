"""managed option sets

Revision ID: 6f0b3c2d4e5a
Revises: 2e8b7a91f6c4
Create Date: 2026-06-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6f0b3c2d4e5a"
down_revision: Union[str, Sequence[str], None] = "2e8b7a91f6c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "managed_option_sets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''")),
        sa.Column("surface", sa.Text(), server_default=sa.text("''")),
        sa.Column("is_system", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_managed_option_sets_key", "managed_option_sets", ["key"], unique=True)
    op.create_index("idx_managed_option_sets_active", "managed_option_sets", ["active"])

    op.create_table(
        "managed_options",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''")),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_placeholder", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metadata_json", sa.Text(), server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_managed_options_set_value", "managed_options", ["set_id", "value"], unique=True)
    op.create_index("idx_managed_options_set_order", "managed_options", ["set_id", "display_order"])
    op.create_index("idx_managed_options_active", "managed_options", ["active"])


def downgrade() -> None:
    op.drop_index("idx_managed_options_active", table_name="managed_options")
    op.drop_index("idx_managed_options_set_order", table_name="managed_options")
    op.drop_index("idx_managed_options_set_value", table_name="managed_options")
    op.drop_table("managed_options")
    op.drop_index("idx_managed_option_sets_active", table_name="managed_option_sets")
    op.drop_index("idx_managed_option_sets_key", table_name="managed_option_sets")
    op.drop_table("managed_option_sets")
