"""In-process tests for the bot-scoped /api/v1/digest endpoint."""
import importlib
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import get_session
from app.models import ActivityLog, WorkTask

BOT_TOKEN = "test-bot-token"


@pytest.fixture
def with_bot_token(monkeypatch):
    """Set a known bot-scope token; reload the import-time token cache."""
    monkeypatch.setenv("TASKTRACK_TOKEN_BOT", BOT_TOKEN)
    from app import tokens
    importlib.reload(tokens)
    yield
    importlib.reload(tokens)


def _iso(d: date) -> str:
    return d.isoformat()


def _seed_tasks(temp_app):
    today = date.today()
    with temp_app.app_context():
        sess = get_session()
        sess.add_all([
            WorkTask(title="Overdue thing", status="In Progress",
                     priority="High", due_date=_iso(today - timedelta(days=2))),
            WorkTask(title="Due today thing", status="Not Started",
                     priority="Medium", due_date=_iso(today)),
            WorkTask(title="Due soon thing", status="Not Started",
                     priority="Low", due_date=_iso(today + timedelta(days=3))),
            WorkTask(title="Far future thing", status="Not Started",
                     priority="Low", due_date=_iso(today + timedelta(days=60))),
            WorkTask(title="Completed overdue", status="Complete",
                     priority="High", due_date=_iso(today - timedelta(days=5))),
            WorkTask(title="No due date", status="In Progress", priority="Medium"),
        ])
        sess.commit()


def test_digest_requires_bot_token(client):
    r = client.get("/api/v1/digest")
    assert r.status_code == 401


def test_digest_buckets_tasks(client, temp_app, with_bot_token):
    _seed_tasks(temp_app)
    r = client.get("/api/v1/digest", headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    body = r.get_json()

    overdue_titles = {i["title"] for i in body["overdue"]}
    due_today_titles = {i["title"] for i in body["due_today"]}
    due_soon_titles = {i["title"] for i in body["due_soon"]}

    assert "Overdue thing" in overdue_titles
    # A Complete task is never surfaced even though its due date has passed.
    assert "Completed overdue" not in overdue_titles
    assert "Due today thing" in due_today_titles
    assert "Due soon thing" in due_soon_titles
    # Beyond the default 7-day horizon.
    assert "Far future thing" not in due_soon_titles
    # 6 seeded, 1 Complete → 5 active.
    assert body["counts"]["active"] == 5
    assert body["counts"]["overdue"] >= 1


def test_digest_due_days_widens_window(client, temp_app, with_bot_token):
    _seed_tasks(temp_app)
    r = client.get("/api/v1/digest?due_days=90", headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    due_soon_titles = {i["title"] for i in r.get_json()["due_soon"]}
    assert "Far future thing" in due_soon_titles  # now inside the window


def test_digest_surfaces_recent_activity(client, temp_app, with_bot_token):
    _seed_tasks(temp_app)
    with temp_app.app_context():
        sess = get_session()
        task = sess.scalars(
            select(WorkTask).where(WorkTask.title == "Overdue thing")
        ).first()
        sess.add(ActivityLog(
            table_name="work_tasks", record_id=task.id,
            action="status_change", field_name="status",
            new_value="In Progress", user_name="Tester",
        ))
        sess.commit()

    r = client.get("/api/v1/digest", headers={"X-Token": BOT_TOKEN})
    body = r.get_json()
    matches = [
        a for a in body["recent_activity"]
        if a["record_title"] == "Overdue thing"
    ]
    assert matches, body["recent_activity"]
    assert matches[0]["action"] == "status_change"


def test_digest_old_activity_excluded(client, temp_app, with_bot_token):
    _seed_tasks(temp_app)
    with temp_app.app_context():
        sess = get_session()
        task = sess.scalars(
            select(WorkTask).where(WorkTask.title == "Overdue thing")
        ).first()
        sess.add(ActivityLog(
            table_name="work_tasks", record_id=task.id,
            action="created", new_value="old",
            created_at=datetime.utcnow() - timedelta(hours=72),
        ))
        sess.commit()

    # Default window is 24h, so a 72h-old entry must not appear.
    r = client.get("/api/v1/digest", headers={"X-Token": BOT_TOKEN})
    actions = {a["action"] for a in r.get_json()["recent_activity"]}
    assert "created" not in actions


def test_monthly_requires_token(client):
    assert client.get("/api/v1/digest/monthly").status_code == 401


def test_monthly_throughput(client, temp_app, with_bot_token):
    today = date.today()
    with temp_app.app_context():
        sess = get_session()
        done = WorkTask(title="Done thing", status="Complete", priority="Medium")
        sess.add(done)
        sess.flush()
        sess.add(WorkTask(title="Open overdue", status="In Progress", priority="High",
                          due_date=(today - timedelta(days=3)).isoformat()))
        sess.add(ActivityLog(table_name="work_tasks", record_id=done.id,
                             action="created", new_value="Done thing"))
        sess.add(ActivityLog(table_name="work_tasks", record_id=done.id,
                             action="status_change", field_name="status",
                             new_value="Complete", user_name="Hermes"))
        sess.commit()
    body = client.get("/api/v1/digest/monthly", headers={"X-Token": BOT_TOKEN}).get_json()
    assert body["throughput"]["created"] >= 1
    assert body["throughput"]["completed"] >= 1
    assert "Done thing" in body["completed_titles"]
    assert body["open"]["overdue"] >= 1
    assert body["open"]["active"] >= 1


# ── funnel-drain signals (#72) ───────────────────────────────────────────────
def test_digest_counts_triage_awaiting(client, temp_app, with_bot_token):
    from app.models import InboxItem
    with temp_app.app_context():
        sess = get_session()
        sess.add_all([
            InboxItem(title="new email request", status="New", source="email"),
            InboxItem(title="another capture", status="New", source="web-form"),
            InboxItem(title="already archived", status="Archived", source="email"),
        ])
        sess.commit()
    body = client.get("/api/v1/digest", headers={"X-Token": BOT_TOKEN}).get_json()
    assert body["counts"]["triage_awaiting"] == 2
    titles = [i["title"] for i in body["triage_awaiting"]]
    assert "new email request" in titles and "already archived" not in titles


def test_digest_counts_parked_work(client, temp_app, with_bot_token):
    stale = datetime.utcnow() - timedelta(days=30)
    with temp_app.app_context():
        sess = get_session()
        old = WorkTask(title="rotting backlog item", status="Not Started")
        fresh = WorkTask(title="fresh capture", status="Not Started")
        active = WorkTask(title="being worked", status="In Progress")
        sess.add_all([old, fresh, active])
        sess.commit()
        # backdate updated_at past the stale window (default 14d)
        old.updated_at = stale
        sess.commit()
    body = client.get("/api/v1/digest", headers={"X-Token": BOT_TOKEN}).get_json()
    assert body["counts"]["parked"] == 1
    assert body["parked"][0]["title"] == "rotting backlog item"
    assert body["stale_days"] == 14


def test_digest_parked_respects_stale_days_param(client, temp_app, with_bot_token):
    with temp_app.app_context():
        sess = get_session()
        t = WorkTask(title="two days old", status="Not Started")
        sess.add(t)
        sess.commit()
        t.updated_at = datetime.utcnow() - timedelta(days=2)
        sess.commit()
    body = client.get("/api/v1/digest?stale_days=1", headers={"X-Token": BOT_TOKEN}).get_json()
    assert body["counts"]["parked"] == 1
    body = client.get("/api/v1/digest?stale_days=10", headers={"X-Token": BOT_TOKEN}).get_json()
    assert body["counts"]["parked"] == 0


def test_digest_redacts_personnel_destined_captures(client, temp_app, with_bot_token):
    from app.models import InboxItem
    with temp_app.app_context():
        sess = get_session()
        sess.add_all([
            InboxItem(title="normal capture", status="New", source="email"),
            InboxItem(title="Conor xref issue again", status="New", source="email",
                      suggested_table="personnel_issues"),
        ])
        sess.commit()
    body = client.get("/api/v1/digest", headers={"X-Token": BOT_TOKEN}).get_json()
    assert body["counts"]["triage_awaiting"] == 2          # count includes it
    assert body["counts"]["triage_redacted"] == 1
    titles = [i["title"] for i in body["triage_awaiting"]]
    assert "normal capture" in titles
    assert all("Conor" not in t for t in titles)           # title never exported


def test_dashboard_funnel_matches_digest(client, temp_app, with_bot_token):
    """The command deck strip and the Slack digest must report the same numbers
    (shared services.funnel)."""
    from app.models import InboxItem
    with temp_app.app_context():
        sess = get_session()
        t = WorkTask(title="old parked", status="Not Started")
        sess.add_all([t, InboxItem(title="waiting", status="New", source="email")])
        sess.commit()
        t.updated_at = datetime.utcnow() - timedelta(days=30)
        sess.commit()
    digest = client.get("/api/v1/digest", headers={"X-Token": BOT_TOKEN}).get_json()
    # dashboard is session-authed: log in via the test client session
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["user_email"] = "t@t"
        s["user_name"] = "T"
        s["user_role"] = "admin"
    dash = client.get("/api/v1/dashboard").get_json()
    assert dash["funnel"]["triage_total"] == digest["counts"]["triage_awaiting"] == 1
    assert dash["funnel"]["parked_total"] == digest["counts"]["parked"] == 1
    assert dash["funnel"]["stale_days"] == 14
