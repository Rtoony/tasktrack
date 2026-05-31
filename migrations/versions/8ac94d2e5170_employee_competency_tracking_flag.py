"""Employee competency tracking flag.

Revision ID: 8ac94d2e5170
Revises: 52d3a8f9b6c1
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8ac94d2e5170"
down_revision: Union[str, Sequence[str], None] = "52d3a8f9b6c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.add_column(sa.Column("competency_tracked", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.create_index("idx_employees_competency_tracked", "employees", ["competency_tracked"])


def downgrade() -> None:
    op.drop_index("idx_employees_competency_tracked", table_name="employees")
    with op.batch_alter_table("employees") as batch:
        batch.drop_column("competency_tracked")
