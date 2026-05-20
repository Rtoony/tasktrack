"""personnel_issues: allow 0/1/many people per incident

Revision ID: e1c47d8a9b22
Revises: d3b67a85ce42
Create Date: 2026-05-20

Phase 5.5 of the eng-ops absorption. Generalises the Capabilities tracker
into a proper incident report:

- `person_name` becomes nullable so reports without an identified person
  are allowed (e.g., process gaps, equipment incidents, anonymous safety
  reports).
- `person_ids` (TEXT JSON array, default '[]') carries the FK linkage to
  one or many Employees. Mirrors the existing `trainee_ids` pattern on
  training_tasks so the generic CRUD path stays flat.

The legacy single-FK `person_id` column stays for backward compatibility
and as a "primary person" hint; the service auto-populates it from
person_ids[0] on insert/update.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1c47d8a9b22'
down_revision: Union[str, Sequence[str], None] = 'd3b67a85ce42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('personnel_issues', schema=None) as batch:
        batch.alter_column(
            'person_name',
            existing_type=sa.Text(),
            nullable=True,
            existing_server_default=None,
        )
        batch.add_column(sa.Column(
            'person_ids', sa.Text(),
            server_default=sa.text("('[]')"), nullable=False,
        ))


def downgrade() -> None:
    with op.batch_alter_table('personnel_issues', schema=None) as batch:
        batch.drop_column('person_ids')
        # Backfill any NULL person_name values before re-imposing NOT NULL.
        # Empty string keeps existing CRUD paths happy.
    op.execute("UPDATE personnel_issues SET person_name = '' WHERE person_name IS NULL")
    with op.batch_alter_table('personnel_issues', schema=None) as batch:
        batch.alter_column(
            'person_name',
            existing_type=sa.Text(),
            nullable=False,
        )
