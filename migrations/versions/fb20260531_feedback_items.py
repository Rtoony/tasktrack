"""Add in-app feedback items.

Revision ID: fb20260531feedback
Revises: 3f8a72b6c901
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fb20260531feedback"
down_revision: Union[str, Sequence[str], None] = "3f8a72b6c901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''")),
        sa.Column("feedback_type", sa.Text(), nullable=False, server_default=sa.text("'Bug'")),
        sa.Column("priority", sa.Text(), nullable=False, server_default=sa.text("'Medium'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'New'")),
        sa.Column("page_url", sa.Text(), server_default=sa.text("''")),
        sa.Column("tab", sa.Text(), server_default=sa.text("''")),
        sa.Column("component_label", sa.Text(), server_default=sa.text("''")),
        sa.Column("context_json", sa.Text(), server_default=sa.text("'{}'")),
        sa.Column("tags", sa.Text(), server_default=sa.text("''")),
        sa.Column("resolution_notes", sa.Text(), server_default=sa.text("''")),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'in-app'")),
        sa.Column("created_by_user_id", sa.Integer()),
        sa.Column("created_by_name", sa.Text(), server_default=sa.text("''")),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.TIMESTAMP()),
    )
    op.create_index("idx_feedback_items_status", "feedback_items", ["status"])
    op.create_index("idx_feedback_items_priority", "feedback_items", ["priority"])
    op.create_index("idx_feedback_items_created_at", "feedback_items", ["created_at"])
    op.create_index("idx_feedback_items_page", "feedback_items", ["page_url"])


def downgrade() -> None:
    op.drop_index("idx_feedback_items_page", table_name="feedback_items")
    op.drop_index("idx_feedback_items_created_at", table_name="feedback_items")
    op.drop_index("idx_feedback_items_priority", table_name="feedback_items")
    op.drop_index("idx_feedback_items_status", table_name="feedback_items")
    op.drop_table("feedback_items")
