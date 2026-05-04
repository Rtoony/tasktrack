"""attachments table + project_number on 4 trackers

Revision ID: 594357560fc3
Revises: b580f3481fd9
Create Date: 2026-05-04

Adds:
  - attachments: polymorphic on (table_name, record_id) like comments /
    activity_log. object_key is the bucket-relative path; sha256 powers
    dedupe at the service layer.
  - project_number TEXT column on work_tasks, training_tasks,
    personnel_issues, suggestion_box. project_work_tasks already has
    one — Josh confirmed it's the same concept and we should reuse it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '594357560fc3'
down_revision: Union[str, Sequence[str], None] = 'b580f3481fd9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PROJECT_NUMBER_TABLES = (
    'work_tasks',
    'training_tasks',
    'personnel_issues',
    'suggestion_box',
)


def upgrade() -> None:
    op.create_table(
        'attachments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('table_name', sa.Text(), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=False),
        sa.Column('object_key', sa.Text(), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('content_type', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.Text(), nullable=False),
        sa.Column('uploaded_by_user_id', sa.Integer(), nullable=True),
        sa.Column('uploaded_by_name', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('uploaded_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('object_key', name='uq_attachments_object_key'),
    )
    op.create_index(
        'idx_attachments_table_record',
        'attachments',
        ['table_name', 'record_id'],
    )

    for table in PROJECT_NUMBER_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(
                'project_number',
                sa.Text(),
                server_default=sa.text("('')"),
                nullable=False,
            ))


def downgrade() -> None:
    for table in PROJECT_NUMBER_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('project_number')
    op.drop_index('idx_attachments_table_record', table_name='attachments')
    op.drop_table('attachments')
