"""Calendar reminder dispatch tests (P1-2)."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import CalendarEvent
from app.services.reminders import (
    dispatch_reminders,
    due_reminders,
    format_reminder,
)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(timespec="minutes")


def _add_event(sess, **overrides) -> CalendarEvent:
    now = datetime.now().replace(microsecond=0)
    defaults = {
        "title": "Ops meeting",
        "event_type": "meeting",
        "start_at": _iso(now + timedelta(days=2)),
        "reminder_date": _iso(now - timedelta(minutes=5)),  # due
        "status": "scheduled",
    }
    defaults.update(overrides)
    row = CalendarEvent(**defaults)
    sess.add(row)
    sess.commit()
    return row


class _Recorder:
    def __init__(self, ok=True):
        self.ok = ok
        self.messages: list[str] = []

    def __call__(self, text: str) -> bool:
        self.messages.append(text)
        return self.ok


def test_due_reminder_is_detected(temp_app):
    with temp_app.app_context():
        sess = get_session()
        row = _add_event(sess)
        due = due_reminders(sess)
        assert [r.id for r in due] == [row.id]


def test_future_reminder_not_yet_due(temp_app):
    with temp_app.app_context():
        sess = get_session()
        now = datetime.now().replace(microsecond=0)
        _add_event(sess, reminder_date=_iso(now + timedelta(hours=2)))
        assert due_reminders(sess) == []


def test_stale_past_event_does_not_fire(temp_app):
    """A reminder due in the past for an event that already happened must not
    trigger a back-reminder (the migration-in guard)."""
    with temp_app.app_context():
        sess = get_session()
        now = datetime.now().replace(microsecond=0)
        _add_event(
            sess,
            start_at=_iso(now - timedelta(days=1)),
            reminder_date=_iso(now - timedelta(days=2)),
        )
        assert due_reminders(sess) == []


def test_done_and_archived_events_skipped(temp_app):
    with temp_app.app_context():
        sess = get_session()
        _add_event(sess, status="done")
        _add_event(sess, status="cancelled")
        archived = _add_event(sess)
        archived.archived_at = datetime.now()
        sess.commit()
        assert due_reminders(sess) == []


def test_dispatch_sends_and_stamps_once(temp_app):
    with temp_app.app_context():
        sess = get_session()
        row = _add_event(sess)
        sender = _Recorder(ok=True)

        first = dispatch_reminders(sess, sender=sender)
        assert first == {"due": 1, "sent": 1, "failed": 0, "ids": [row.id]}
        assert len(sender.messages) == 1
        assert "Ops meeting" in sender.messages[0]

        sess.refresh(row)
        assert row.reminder_sent_at is not None

        # Second sweep: already stamped -> nothing due, no second send.
        second = dispatch_reminders(sess, sender=sender)
        assert second["due"] == 0
        assert second["sent"] == 0
        assert len(sender.messages) == 1


def test_failed_send_is_not_stamped_and_retries(temp_app):
    with temp_app.app_context():
        sess = get_session()
        row = _add_event(sess)
        failing = _Recorder(ok=False)

        result = dispatch_reminders(sess, sender=failing)
        assert result["due"] == 1
        assert result["sent"] == 0
        assert result["failed"] == 1

        sess.refresh(row)
        assert row.reminder_sent_at is None  # not stamped -> will retry

        # A later successful sweep does send it.
        ok = _Recorder(ok=True)
        retry = dispatch_reminders(sess, sender=ok)
        assert retry["sent"] == 1
        sess.refresh(row)
        assert row.reminder_sent_at is not None


def test_format_reminder_includes_key_fields(temp_app):
    with temp_app.app_context():
        sess = get_session()
        row = _add_event(
            sess,
            title="Bridge kickoff",
            project_number="1234.56",
            location="Conf Room",
            description="Bring the survey",
        )
        text = format_reminder(row)
        assert "Bridge kickoff" in text
        assert "1234.56" in text
        assert "Conf Room" in text
        assert "Bring the survey" in text
