"""calendar events

Revision ID: 19f4c8b2a6d0
Revises: 7c9a1e3f4d2b
Create Date: 2026-05-26

Internal operations calendar replacing the retired external personal
calendar/Radicale widget. Dates are stored as ISO TEXT for consistency
with existing TaskTrack trackers and SQLite portability.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "19f4c8b2a6d0"
down_revision: Union[str, Sequence[str], None] = "7c9a1e3f4d2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text(), server_default=sa.text("'meeting'"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=True),
        sa.Column("start_at", sa.Text(), nullable=False),
        sa.Column("end_at", sa.Text(), server_default=sa.text("''"), nullable=True),
        sa.Column("all_day", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'scheduled'"), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("project_number", sa.Text(), server_default=sa.text("''"), nullable=True),
        sa.Column("related_table", sa.Text(), server_default=sa.text("''"), nullable=True),
        sa.Column("related_id", sa.Integer(), nullable=True),
        sa.Column("reminder_date", sa.Text(), server_default=sa.text("''"), nullable=True),
        sa.Column("location", sa.Text(), server_default=sa.text("''"), nullable=True),
        sa.Column("visibility", sa.Text(), server_default=sa.text("'internal'"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.Text(), server_default=sa.text("''"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("idx_calendar_events_start_at", "calendar_events", ["start_at"])
    op.create_index("idx_calendar_events_status", "calendar_events", ["status"])
    op.create_index("idx_calendar_events_event_type", "calendar_events", ["event_type"])
    op.create_index("idx_calendar_events_project_id", "calendar_events", ["project_id"])
    op.create_index("idx_calendar_events_related", "calendar_events", ["related_table", "related_id"])


def downgrade() -> None:
    op.drop_index("idx_calendar_events_related", table_name="calendar_events")
    op.drop_index("idx_calendar_events_project_id", table_name="calendar_events")
    op.drop_index("idx_calendar_events_event_type", table_name="calendar_events")
    op.drop_index("idx_calendar_events_status", table_name="calendar_events")
    op.drop_index("idx_calendar_events_start_at", table_name="calendar_events")
    op.drop_table("calendar_events")
