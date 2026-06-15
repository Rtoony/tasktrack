"""reminder dispatch tracking on calendar_events (P1-2).

Revision ID: d8f1c4a7e620
Revises: c5d9b2a7e413
Create Date: 2026-06-15

P1-2 (WORK_PLAN "#1 missing adoption driver"): `calendar_events.reminder_date`
has existed since the calendar_events migration with NO dispatch logic — a
reminder was stored but never sent. This adds a single `reminder_sent_at`
TIMESTAMP column so the reminder sweep (a systemd-timer-driven Flask CLI
command) can stamp an event once its reminder has been delivered and never
re-send it on the next sweep. NULL = not yet sent.

Additive and non-destructive; no backfill (existing events with a past
reminder_date will be treated as already-due on the first sweep, so the
sweep itself only sends reminders whose `start_at` is still in the future —
see app/services/reminders.py — to avoid a burst of stale back-reminders).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8f1c4a7e620"
down_revision: Union[str, Sequence[str], None] = "c5d9b2a7e413"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.add_column(sa.Column("reminder_sent_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.drop_column("reminder_sent_at")
