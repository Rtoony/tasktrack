"""projects: vanished_from_master_at + last_seen_in_master_at

Revision ID: a4e317c5b69d
Revises: f2a96c4d0b13
Create Date: 2026-05-22

Vanish-tracking columns for the automated master-list sync prototype.

- `last_seen_in_master_at` (TEXT, default '') — stamped by every
  successful import for every project_number found in that run's Excel.
  Blank means the project has never been observed in any master-list
  run (true today for everything that landed before this migration —
  the initial bulk import is treated by the wrapper as "first sight"
  and will populate the column).

- `vanished_from_master_at` (TEXT, default '') — stamped the first
  sync after a previously-seen project no longer appears in the Excel.
  Used by the sync wrapper to flip `display_status` to `dormant` and
  by the admin UI to highlight stale rows. Cleared back to '' if the
  project reappears in a later master-list run.

Both columns are TEXT (ISO 8601 strings) to stay consistent with
start_date/dormant_date and the rest of TaskTrack's date columns.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a4e317c5b69d'
down_revision: Union[str, Sequence[str], None] = 'f2a96c4d0b13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch:
        batch.add_column(sa.Column(
            'last_seen_in_master_at', sa.Text(),
            server_default=sa.text("('')"), nullable=False,
        ))
        batch.add_column(sa.Column(
            'vanished_from_master_at', sa.Text(),
            server_default=sa.text("('')"), nullable=False,
        ))
        batch.create_index(
            'idx_projects_vanished_from_master_at',
            ['vanished_from_master_at'],
        )


def downgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch:
        batch.drop_index('idx_projects_vanished_from_master_at')
        batch.drop_column('vanished_from_master_at')
        batch.drop_column('last_seen_in_master_at')
