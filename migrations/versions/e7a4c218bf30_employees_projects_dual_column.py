"""employees + projects spine (dual-column FK refactor)

Revision ID: e7a4c218bf30
Revises: d1f0b2a36c11
Create Date: 2026-05-19

Phase 0 of the eng-ops absorption (see ~/.claude/plans/tingly-crafting-seal.md).
Adds two new tables — `employees` and `projects` — plus nullable FK columns
on the existing trackers that have free-text engineer / trainees / person_name /
project_number fields.

Design notes
- Text columns stay untouched and authoritative for one or more release cycles.
- New FK columns are nullable. AI triage continues to write text; an enrichment
  hook in services/tickets.py does best-effort exact-name lookup to set the FK.
- `trainee_ids` on training_tasks is a JSON-array TEXT column (no junction
  table yet — multi-staff is low-volume and keeping the schema flat means the
  generic CRUD path keeps working).
- `external_ref` / `external_system` on projects are slots for a future
  external project registry (eng-ops's Atlas pattern) without committing to it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e7a4c218bf30'
down_revision: Union[str, Sequence[str], None] = 'd1f0b2a36c11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'employees',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('role', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('title', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('active', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('notes', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_employees_active', 'employees', ['active'])
    op.create_index('idx_employees_display_name', 'employees', ['display_name'])

    op.create_table(
        'projects',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_number', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('client', sa.Text(), server_default=sa.text("('')"), nullable=False),
        sa.Column('billing_phase_default', sa.Text(),
                  server_default=sa.text("('')"), nullable=False),
        sa.Column('active', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('external_ref', sa.Text(),
                  server_default=sa.text("('')"), nullable=False),
        sa.Column('external_system', sa.Text(),
                  server_default=sa.text("('')"), nullable=False),
        sa.Column('notes', sa.Text(),
                  server_default=sa.text("('')"), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_projects_project_number', 'projects',
                    ['project_number'], unique=True)
    op.create_index('idx_projects_active', 'projects', ['active'])

    # FK columns. Nullable. Plain INTEGER — SQLite doesn't enforce REFERENCES
    # without PRAGMA foreign_keys=ON, and we want this migration to be
    # reversible without cascading deletes.
    with op.batch_alter_table('work_tasks', schema=None) as batch:
        batch.add_column(sa.Column('project_id', sa.Integer(), nullable=True))

    with op.batch_alter_table('project_work_tasks', schema=None) as batch:
        batch.add_column(sa.Column('project_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('engineer_id', sa.Integer(), nullable=True))

    with op.batch_alter_table('training_tasks', schema=None) as batch:
        batch.add_column(sa.Column('project_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column(
            'trainee_ids', sa.Text(),
            server_default=sa.text("('[]')"), nullable=False,
        ))

    with op.batch_alter_table('personnel_issues', schema=None) as batch:
        batch.add_column(sa.Column('project_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('person_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('personnel_issues', schema=None) as batch:
        batch.drop_column('person_id')
        batch.drop_column('project_id')

    with op.batch_alter_table('training_tasks', schema=None) as batch:
        batch.drop_column('trainee_ids')
        batch.drop_column('project_id')

    with op.batch_alter_table('project_work_tasks', schema=None) as batch:
        batch.drop_column('engineer_id')
        batch.drop_column('project_id')

    with op.batch_alter_table('work_tasks', schema=None) as batch:
        batch.drop_column('project_id')

    op.drop_index('idx_projects_active', table_name='projects')
    op.drop_index('idx_projects_project_number', table_name='projects')
    op.drop_table('projects')

    op.drop_index('idx_employees_display_name', table_name='employees')
    op.drop_index('idx_employees_active', table_name='employees')
    op.drop_table('employees')
