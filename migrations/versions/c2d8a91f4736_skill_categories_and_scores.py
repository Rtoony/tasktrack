"""skill_categories + employee_skill_scores (Competency, Phase 1)

Revision ID: c2d8a91f4736
Revises: b8d51c92e711
Create Date: 2026-05-20

Phase 1 of the eng-ops absorption (~/.claude/plans/tingly-crafting-seal.md).
Adds the Competency pillar's two tables. Per-cell score-change history
is reused via the existing polymorphic activity_log (no new history
table needed).

- skill_categories: small taxonomy table. Seeded with the same 10
  categories eng-ops uses so the rubric is consistent across tools.
- employee_skill_scores: one row per (employee, category) pair.
  Score is REAL 1.0-10.0 with half-step granularity. Upserts only;
  no soft-delete (the row's existence IS the assertion).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c2d8a91f4736'
down_revision: Union[str, Sequence[str], None] = 'b8d51c92e711'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'skill_categories',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(),
                  server_default=sa.text("('')"), nullable=False),
        sa.Column('display_order', sa.Integer(),
                  server_default=sa.text('0'), nullable=False),
        sa.Column('active', sa.Integer(),
                  server_default=sa.text('1'), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_skill_categories_slug', 'skill_categories',
                    ['slug'], unique=True)
    op.create_index('idx_skill_categories_active', 'skill_categories', ['active'])

    op.create_table(
        'employee_skill_scores',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.Column('score', sa.Float(),
                  server_default=sa.text('5.0'), nullable=False),
        sa.Column('notes', sa.Text(),
                  server_default=sa.text("('')"), nullable=False),
        sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_emp_skill_employee', 'employee_skill_scores',
                    ['employee_id'])
    op.create_index('idx_emp_skill_category', 'employee_skill_scores',
                    ['category_id'])
    op.create_index('idx_emp_skill_unique', 'employee_skill_scores',
                    ['employee_id', 'category_id'], unique=True)


def downgrade() -> None:
    op.drop_index('idx_emp_skill_unique', table_name='employee_skill_scores')
    op.drop_index('idx_emp_skill_category', table_name='employee_skill_scores')
    op.drop_index('idx_emp_skill_employee', table_name='employee_skill_scores')
    op.drop_table('employee_skill_scores')

    op.drop_index('idx_skill_categories_active', table_name='skill_categories')
    op.drop_index('idx_skill_categories_slug', table_name='skill_categories')
    op.drop_table('skill_categories')
