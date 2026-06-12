"""work_tasks.category + 'None' task priority.

Revision ID: c7e2f5a1d4b9
Revises: b9e4a7c3d2f8
Create Date: 2026-06-12

Feedback #24: the CAD Dev tracker gets a work-stream `category` field
(CAD Standards Portal, LISP / Automation, ...) distinct from the
skill/discipline axis (`cad_skill_area`). The vocabulary lives in the
managed option set `work_category`, which seed_default_option_sets()
creates at boot — only the column is added here.

Feedback #28: a fourth priority 'None' (no row accent, sorts last).
seed_default_option_sets() deliberately never adds new options to an
existing set (admin deletions must stick), so the live task_priority
set gets its 'None' option inserted here, idempotently.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7e2f5a1d4b9"
down_revision: Union[str, Sequence[str], None] = "b9e4a7c3d2f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("work_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("category", sa.Text(), nullable=False, server_default="")
        )

    op.execute(
        """
        INSERT INTO managed_options
            (set_id, value, label, display_order, active, is_placeholder, metadata_json)
        SELECT s.id, 'None', 'None', 5, 1, 0, '{"rank": 40, "tone": "muted"}'
        FROM managed_option_sets s
        WHERE s.key = 'task_priority'
          AND NOT EXISTS (
              SELECT 1 FROM managed_options o
              WHERE o.set_id = s.id AND o.value = 'None'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM managed_options
        WHERE value = 'None'
          AND set_id IN (SELECT id FROM managed_option_sets WHERE key = 'task_priority')
        """
    )
    with op.batch_alter_table("work_tasks") as batch_op:
        batch_op.drop_column("category")
