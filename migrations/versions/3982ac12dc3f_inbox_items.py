"""replace personal_tasks with inbox_items

Revision ID: 3982ac12dc3f
Revises: 4393e97de07d
Create Date: 2026-05-04

The personal_tasks table (Maximus quick-capture) is going away — its
0-row body of work transfers to the new inbox_items, which serves as
the unified capture surface for the whole Nexus suite. Schema is
deliberately leaner: no category/recurrence churn, no completed_at
overload, plain status_flow that mirrors the other trackers.

inbox_items can either live forever as personal todo items OR be
promoted into one of the 5 task trackers (promoted_to_table /
promoted_to_id record where they ended up).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3982ac12dc3f'
down_revision: Union[str, Sequence[str], None] = '4393e97de07d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('idx_personal_tasks_status', table_name='personal_tasks')
    op.drop_index('idx_personal_tasks_completed', table_name='personal_tasks')
    op.drop_table('personal_tasks')

    op.create_table(
        'inbox_items',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('body', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('source', sa.Text(), server_default=sa.text("('manual')"), nullable=False),
        sa.Column('source_ref', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("('New')"), nullable=False),
        sa.Column('priority', sa.Text(), server_default=sa.text("('Medium')"), nullable=False),
        sa.Column('due_date', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('promoted_to_table', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('promoted_to_id', sa.Integer(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_by_name', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('completed_at', sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_inbox_items_status', 'inbox_items', ['status'])
    op.create_index('idx_inbox_items_source_ref', 'inbox_items', ['source', 'source_ref'])


def downgrade() -> None:
    op.drop_index('idx_inbox_items_source_ref', table_name='inbox_items')
    op.drop_index('idx_inbox_items_status', table_name='inbox_items')
    op.drop_table('inbox_items')

    op.create_table(
        'personal_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('category', sa.Text(), server_default=sa.text("'Personal'"), nullable=False),
        sa.Column('priority', sa.Text(), server_default=sa.text("'Medium'"), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("'Not Started'"), nullable=False),
        sa.Column('due_date', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('recurrence', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('notes', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('source', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('completed_at', sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_personal_tasks_status', 'personal_tasks', ['status'])
    op.create_index('idx_personal_tasks_completed', 'personal_tasks', ['completed_at'])
