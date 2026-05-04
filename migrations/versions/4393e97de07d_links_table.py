"""links table — per-record hyperlinks

Revision ID: 4393e97de07d
Revises: 594357560fc3
Create Date: 2026-05-04

Polymorphic on (table_name, record_id) like attachments / comments /
activity_log. Stores the URL plus a friendly label (auto-derived from
URL recognizers in app/services/links.py, or user-typed).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4393e97de07d'
down_revision: Union[str, Sequence[str], None] = '594357560fc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'links',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('table_name', sa.Text(), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('source_kind', sa.Text(), server_default=sa.text("('generic')"), nullable=False),
        sa.Column('added_by_user_id', sa.Integer(), nullable=True),
        sa.Column('added_by_name', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_links_table_record', 'links', ['table_name', 'record_id'])


def downgrade() -> None:
    op.drop_index('idx_links_table_record', table_name='links')
    op.drop_table('links')
