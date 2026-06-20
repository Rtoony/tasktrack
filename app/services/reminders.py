"""Calendar reminder dispatch (P1-2).

WORK_PLAN names reminders the "#1 missing adoption driver":
``calendar_events.reminder_date`` has stored a reminder timestamp since the
calendar_events migration, but nothing ever *acted* on it. This module is the
trigger logic — a sweep that finds events whose reminder is now due and sends
the operator a Telegram nudge, stamping ``reminder_sent_at`` so each reminder
fires exactly once.

It is deliberately split into two pieces:

- ``due_reminders(session, now)`` — pure query, no I/O. Returns the events that
  should fire on this sweep. Unit-testable without Telegram.
- ``dispatch_reminders(session, now, sender)`` — formats each due reminder,
  calls ``sender(text)`` once per event, and stamps ``reminder_sent_at`` only
  for the ones the sender accepted. The default sender reuses the existing
  ``scripts/notify_master_sync.send_telegram`` path (same bot token + chat-id
  env contract as the master-sync digest) — no new bot is invented.

A reminder is *due* when all of these hold:
  * the event is not archived and not done/cancelled,
  * ``reminder_date`` parses and is <= now,
  * ``reminder_sent_at`` is NULL (not already sent),
  * the event's ``start_at`` is still in the future (all-day: today or later).
    The last guard means migrating in with old past reminders does NOT trigger
    a burst of stale back-reminders — only live, upcoming events nudge.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select

from ..models import CalendarEvent

LOG = logging.getLogger("tasktrack.reminders")

_DONE_STATUSES = {"done", "cancelled"}


def _parse_iso(raw: str | None) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _event_is_future(row: CalendarEvent, now: datetime) -> bool:
    """Is the event itself still upcoming (so a reminder is still useful)?"""
    start = _parse_iso(row.start_at)
    if start is None:
        return False
    if row.all_day:
        return start.date() >= now.date()
    return start >= now


def due_reminders(session, now: datetime | None = None) -> list[CalendarEvent]:
    """Return events whose reminder is due and not yet sent. Pure, no I/O."""
    now = now or datetime.now()
    rows = session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.archived_at.is_(None),
            CalendarEvent.reminder_sent_at.is_(None),
        )
    ).all()

    due: list[CalendarEvent] = []
    for row in rows:
        if row.status in _DONE_STATUSES:
            continue
        reminder = _parse_iso(row.reminder_date)
        if reminder is None or reminder > now:
            continue
        if not _event_is_future(row, now):
            continue
        due.append(row)
    # Soonest-starting first so the message order matches urgency.
    due.sort(key=lambda r: (_parse_iso(r.start_at) or now))
    return due


def format_reminder(row: CalendarEvent) -> str:
    """Render the single-event Telegram reminder body (plain text)."""
    start = _parse_iso(row.start_at)
    when = ""
    if start is not None:
        when = start.strftime("%a %b %-d") if row.all_day else start.strftime("%a %b %-d %H:%M")
    lines = [f"⏰ TaskTrack reminder: {row.title}"]
    if when:
        lines.append(f"   {row.event_type or 'event'} · {when}")
    if row.project_number:
        lines.append(f"   Project {row.project_number}")
    if row.location:
        lines.append(f"   @ {row.location}")
    if row.description:
        body = row.description.strip()
        if len(body) > 300:
            body = body[:297] + "…"
        lines.append(f"   {body}")
    return "\n".join(lines)


def _default_sender(text: str) -> bool:
    """Send one reminder on BOTH legs during the Telegram→Slack parallel phase.

    Slack: via nexus-notify (@sentinel) to ``SLACK_REMINDERS_CHANNEL`` if that
    env is set, else the default #alerts channel. Telegram: the existing
    master-sync path. Imported/spawned lazily so importing this module (e.g. in
    tests) never drags in the scripts package or requires messaging env.
    Returns True if EITHER leg delivered, so the reminder is stamped (not
    re-sent) once it's out on at least one channel; False only if both fail
    (retried next sweep).
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    # --- Slack leg (additive). Best-effort; never raises. ---
    slack_ok = False
    try:
        cmd = ["/home/rtoony/bin/nexus-notify", "--bot=sentinel", "--slack-only"]
        chan = os.environ.get("SLACK_REMINDERS_CHANNEL", "").strip()
        if chan:
            cmd += ["--slack-channel", chan]
        cmd.append(text)
        slack_ok = subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
        if not slack_ok:
            LOG.warning("reminder slack leg failed (nexus-notify returned nonzero)")
    except Exception as exc:  # noqa: BLE001
        LOG.warning("reminder slack leg error: %s", exc)

    # --- Telegram leg (existing master-sync path). ---
    tg_ok = False
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from notify_master_sync import send_telegram  # type: ignore

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CLAUDE_CHAT_ID", "").strip()
        if token and chat_id:
            tg_ok = bool(send_telegram(text, token=token, chat_id=chat_id))
        else:
            LOG.warning(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CLAUDE_CHAT_ID not set; "
                "telegram reminder leg skipped"
            )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("reminder telegram sender unavailable: %s", exc)

    return slack_ok or tg_ok


def dispatch_reminders(
    session,
    now: datetime | None = None,
    sender: Callable[[str], bool] | None = None,
) -> dict:
    """Send all due reminders and stamp the ones the sender accepted.

    Only events whose send succeeds get ``reminder_sent_at`` set, so a
    transient Telegram failure (or missing creds) means the reminder is
    retried on the next sweep instead of being silently dropped.

    Returns a summary dict: {"due": N, "sent": M, "failed": K, "ids": [...]}.
    """
    now = now or datetime.now()
    sender = sender or _default_sender
    due = due_reminders(session, now)

    sent_ids: list[int] = []
    failed = 0
    for row in due:
        text = format_reminder(row)
        try:
            ok = bool(sender(text))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("reminder send raised for event %s: %s", row.id, exc)
            ok = False
        if ok:
            row.reminder_sent_at = now
            sent_ids.append(row.id)
        else:
            failed += 1

    if sent_ids:
        session.commit()

    return {
        "due": len(due),
        "sent": len(sent_ids),
        "failed": failed,
        "ids": sent_ids,
    }
