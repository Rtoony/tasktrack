"""W8 (last-week calendar anchor + week-over-week delta) and W9 (always-on
"stuck" 31d+ count + net-backlog framing) tests.

Kept in a separate module from test_weekly.py so the W8/W9 polish work has a
self-contained acceptance suite. Exercises the pure-data aggregator directly
plus the route layer (?week_offset=) via the Flask test client.
"""
from datetime import UTC, datetime, timedelta

from app.db import get_session
from app.models import ActivityLog, WorkTask
from app.services.weekly import weekly_snapshot


def _naive_utc_days_ago(days: float) -> datetime:
    """A naive-UTC datetime `days` in the past — SQLite stores naive UTC, so
    this matches how the app writes created_at."""
    return datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=days)


# ── W8: `until` bounds the upper edge of the window ──────────────────────────


def test_until_excludes_rows_created_after_it(temp_app):
    """A row created AFTER `until` must not count as created-in-window, while a
    row created inside (since, until] still does. This is the core W8 bound that
    makes viewing a *past* week possible (the live window stops at `until`, not
    now)."""
    with temp_app.app_context():
        sess = get_session()
        inside = WorkTask(title="inside-window")
        after = WorkTask(title="after-window")
        sess.add_all([inside, after])
        sess.flush()
        # Window: 14 days ago .. 7 days ago. `inside` lands at day-10, `after`
        # is fresh (now) — i.e. after the until bound.
        inside.created_at = _naive_utc_days_ago(10)
        after.created_at = _naive_utc_days_ago(0.01)
        sess.commit()

        since = datetime.now(tz=UTC) - timedelta(days=14)
        until = datetime.now(tz=UTC) - timedelta(days=7)
        snap = weekly_snapshot(sess, since=since, until=until)

    work = snap["buckets"]["work_tasks"]
    titles = {i["title"] for i in work["items_created"]}
    assert "inside-window" in titles
    assert "after-window" not in titles, "row created after `until` leaked in"
    assert work["created"] == 1


def test_until_bounds_completed_in_window(temp_app):
    """A status_change INTO done logged AFTER `until` must not count as completed
    in the bounded window."""
    with temp_app.app_context():
        sess = get_session()
        row = WorkTask(title="done-after-until", status="Complete")
        sess.add(row)
        sess.flush()
        # Transition logged "now" — after a window that ends 7 days ago.
        sess.add(ActivityLog(
            table_name="work_tasks", record_id=row.id, action="status_change",
            field_name="status", old_value="In Progress", new_value="Complete",
            created_at=_naive_utc_days_ago(0.01),
        ))
        sess.commit()
        since = datetime.now(tz=UTC) - timedelta(days=14)
        until = datetime.now(tz=UTC) - timedelta(days=7)
        snap = weekly_snapshot(sess, since=since, until=until)
    assert snap["buckets"]["work_tasks"]["completed"] == 0


# ── W8: default-call payload shape is unchanged (no until/week_offset) ────────


def test_default_call_totals_shape_unchanged(temp_app):
    """The `totals` dict MUST keep exactly its four locked keys — the new W9
    stuck_count/net_backlog live at snapshot top level, never inside totals,
    so pre-W8 consumers of `totals` are untouched."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="x", status="In Progress"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    assert set(snap["totals"].keys()) == {
        "created", "completed", "active_now", "overdue_now",
    }


def test_default_call_bucket_shape_has_no_breakdown(temp_app):
    """Default (no breakdown) buckets carry stuck_count but NOT a breakdown
    block — the opt-in rollups stay opt-in."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="x", status="In Progress"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    bucket = snap["buckets"]["work_tasks"]
    assert "breakdown" not in bucket
    assert "stuck_count" in bucket


# ── W8: week-over-week delta is exposed and correct ──────────────────────────


def test_wow_delta_present_and_signed(temp_app):
    """With more created this window than the prior equal window, wow_delta
    reports a positive `created` delta."""
    with temp_app.app_context():
        sess = get_session()
        # 2 created this 7-day window.
        sess.add(WorkTask(title="new-a"))
        sess.add(WorkTask(title="new-b"))
        # 1 created in the prior 7-day window (8-14 days ago).
        prior = WorkTask(title="prior-1")
        sess.add(prior)
        sess.flush()
        prior.created_at = _naive_utc_days_ago(10)
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    assert "wow_delta" in snap
    assert "prior_window" in snap
    assert snap["totals"]["created"] == 2
    assert snap["prior_window"]["created"] == 1
    assert snap["wow_delta"]["created"] == 1  # 2 this - 1 prior


# ── W9: stuck_count is always-on and counts a 40-day-old active row ──────────


def test_stuck_count_present_by_default(temp_app):
    """stuck_count exists at the snapshot top level on a plain default call
    (NOT gated on breakdown)."""
    with temp_app.app_context():
        sess = get_session()
        snap = weekly_snapshot(sess, days=7)
    assert "stuck_count" in snap
    assert isinstance(snap["stuck_count"], int)
    assert "net_backlog" in snap


def test_stuck_count_counts_40_day_old_active_row(temp_app):
    """A 40-day-old still-active row is "stuck" (31d+); a fresh active row and a
    40-day-old DONE row are not. stuck_count == 1."""
    with temp_app.app_context():
        sess = get_session()
        old_active = WorkTask(title="old-stuck", status="In Progress")
        fresh = WorkTask(title="fresh", status="In Progress")
        old_done = WorkTask(title="old-done", status="Complete")
        sess.add_all([old_active, fresh, old_done])
        sess.flush()
        old_active.created_at = _naive_utc_days_ago(40)
        old_done.created_at = _naive_utc_days_ago(40)
        sess.commit()
        snap = weekly_snapshot(sess, days=7)

    assert snap["stuck_count"] == 1
    assert snap["buckets"]["work_tasks"]["stuck_count"] == 1
    # And it equals the breakdown's 31d+ open count — same machinery, promoted.
    bd_snap = None
    with temp_app.app_context():
        bd_snap = weekly_snapshot(get_session(), days=7, breakdown=True)
    assert bd_snap["buckets"]["work_tasks"]["breakdown"]["age_buckets"]["31d+"] == 1


def test_net_backlog_is_created_minus_completed(temp_app):
    """net_backlog == created - completed across the window (non-punitive
    framing of net change in active inventory)."""
    with temp_app.app_context():
        sess = get_session()
        # 3 created this window, 1 completed this window.
        a = WorkTask(title="c1", status="In Progress")
        b = WorkTask(title="c2", status="In Progress")
        c = WorkTask(title="c3", status="Complete")
        sess.add_all([a, b, c])
        sess.flush()
        sess.add(ActivityLog(
            table_name="work_tasks", record_id=c.id, action="status_change",
            field_name="status", old_value="In Progress", new_value="Complete",
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    t = snap["totals"]
    assert snap["net_backlog"] == t["created"] - t["completed"]
    assert snap["net_backlog"] == 3 - 1


# ── Route layer: ?week_offset= and rendered strings ──────────────────────────


def test_week_offset_route_bounds_a_past_calendar_week(auth_client):
    """?week_offset=1 returns a JSON snapshot whose window is a 7-day calendar
    week (Mon-Sun) ending before now — until is in the past, span is 7 days."""
    body = auth_client.get("/api/v1/weekly?week_offset=1").get_json()
    assert body["days"] == 7
    since = datetime.fromisoformat(body["since"])
    until = datetime.fromisoformat(body["until"])
    assert (until - since) == timedelta(days=7)
    # The chosen week is fully in the past (its end is <= now).
    assert until <= datetime.now(tz=UTC) + timedelta(seconds=5)
    # Anchored on a Monday 00:00 UTC.
    assert since.weekday() == 0
    assert (since.hour, since.minute, since.second) == (0, 0, 0)


def test_week_offset_garbage_falls_back_to_trailing_window(auth_client):
    """A non-integer week_offset falls back to the default trailing window
    (days honored, not a calendar week)."""
    body = auth_client.get("/api/v1/weekly?week_offset=banana&days=14").get_json()
    assert body["days"] == 14


def test_weekly_html_shows_stuck_and_wow_strings(auth_client, temp_app):
    """The HTML page surfaces the W9 "Stuck (31d+)" headline and a W8
    week-over-week delta line by default."""
    with temp_app.app_context():
        sess = get_session()
        old_active = WorkTask(title="old-stuck-row", status="In Progress")
        sess.add(old_active)
        sess.flush()
        old_active.created_at = _naive_utc_days_ago(40)
        sess.commit()

    r = auth_client.get("/weekly")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Stuck (31d+)" in html
    assert "vs prior week" in html
    assert "Net backlog" in html


def test_weekly_html_week_offset_renders_picker(auth_client):
    """/weekly?week_offset=1 renders 200 with the calendar-week framing and the
    'This week' return link in the picker."""
    r = auth_client.get("/weekly?week_offset=1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "calendar week" in html
    assert "This week" in html  # picker link back to the live window
