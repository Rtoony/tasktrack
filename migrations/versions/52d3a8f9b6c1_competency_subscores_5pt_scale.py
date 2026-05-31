"""Competency subscores and 5-point scale.

Revision ID: 52d3a8f9b6c1
Revises: fb20260531feedback
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "52d3a8f9b6c1"
down_revision: Union[str, Sequence[str], None] = "fb20260531feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employee_skill_subscores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("dimension_slug", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("observed_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("source_kind", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("source_id", sa.Integer()),
        sa.Column("notes", sa.Text(), server_default=sa.text("''")),
        sa.Column("created_by_user_id", sa.Integer()),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_skill_subscore_dim", "employee_skill_subscores", ["employee_id", "category_id", "dimension_slug"])
    op.create_index("idx_skill_subscore_observed", "employee_skill_subscores", ["employee_id", "category_id", "observed_at"])
    op.create_index("idx_skill_subscore_source", "employee_skill_subscores", ["source_kind", "source_id"])

    with op.batch_alter_table("employee_skill_scores") as batch:
        batch.add_column(sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")))
        batch.add_column(sa.Column("sample_size", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch.add_column(sa.Column("last_observed_at", sa.TIMESTAMP()))
        batch.add_column(sa.Column("rollup_version", sa.Integer(), nullable=False, server_default=sa.text("1")))

    op.execute(
        """
        UPDATE employee_skill_scores
           SET score = MIN(5.0, MAX(1.0, ROUND(score / 2.0 * 2.0) / 2.0)),
               confidence = 0.3,
               sample_size = 1,
               last_observed_at = updated_at,
               rollup_version = 1
        """
    )
    op.execute(
        """
        INSERT INTO employee_skill_subscores
            (employee_id, category_id, dimension_slug, score, weight, observed_at,
             source_kind, source_id, notes, created_by_user_id, created_at)
        SELECT employee_id, category_id, 'manual', score, 1.0,
               COALESCE(updated_at, CURRENT_TIMESTAMP), 'legacy_rescale', id,
               COALESCE(notes, ''), updated_by_user_id, CURRENT_TIMESTAMP
          FROM employee_skill_scores
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("employee_skill_scores") as batch:
        batch.drop_column("rollup_version")
        batch.drop_column("last_observed_at")
        batch.drop_column("sample_size")
        batch.drop_column("confidence")

    op.drop_index("idx_skill_subscore_source", table_name="employee_skill_subscores")
    op.drop_index("idx_skill_subscore_observed", table_name="employee_skill_subscores")
    op.drop_index("idx_skill_subscore_dim", table_name="employee_skill_subscores")
    op.drop_table("employee_skill_subscores")
