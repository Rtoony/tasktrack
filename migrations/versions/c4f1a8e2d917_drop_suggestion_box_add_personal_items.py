"""drop suggestion_box, add personal_items

Revision ID: c4f1a8e2d917
Revises: 3982ac12dc3f
Create Date: 2026-05-04

Single-user personal pivot: the firm-shop "Suggestion Box" is gone, and a
new personal_items table backs four UI tabs (Husband / Father / House /
Cars) via a category column. Schema mirrors inbox_items but adds the
required category field; the existing /api/v1/inbox/<id>/promote
endpoint already plumbs target_category through `overrides`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f1a8e2d917'
down_revision: Union[str, Sequence[str], None] = '3982ac12dc3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('suggestion_box')

    op.create_table(
        'personal_items',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('priority', sa.Text(), server_default=sa.text("('Medium')"), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("('New')"), nullable=False),
        sa.Column('due_date', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('source', sa.Text(), server_default=sa.text("('manual')"), nullable=False),
        sa.Column('source_ref', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_by_name', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('completed_at', sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_personal_items_category', 'personal_items', ['category'])
    op.create_index('idx_personal_items_status', 'personal_items', ['status'])


def downgrade() -> None:
    op.drop_index('idx_personal_items_status', table_name='personal_items')
    op.drop_index('idx_personal_items_category', table_name='personal_items')
    op.drop_table('personal_items')

    op.create_table(
        'suggestion_box',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('suggestion_type', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('submitted_by', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('submitted_for', sa.Text(), server_default=sa.text("('Management')"), nullable=False),
        sa.Column('summary', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('expected_value', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('priority', sa.Text(), server_default=sa.text("('Medium')"), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("('New')"), nullable=False),
        sa.Column('review_notes', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('promoted_work_task_id', sa.Integer(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_by_name', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('project_number', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
