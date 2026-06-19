"""In-process tests for the bot-scoped /api/v1/changes change-feed."""
import importlib
from datetime import datetime, timedelta

import pytest

from app.db import get_session
from app.models import ActivityLog, FeedbackItem, PersonnelIssue, WorkTask

BOT_TOKEN = "test-bot-token"


@pytest.fixture
def with_bot_token(monkeypatch):
    monkeypatch.setenv("TASKTRACK_TOKEN_BOT", BOT_TOKEN)
    from app import tokens
    importlib.reload(tokens)
    yield
    importlib.reload(tokens)


def _seed_change(temp_app, *, table="work_tasks", action="status_change",
                 new_value="Complete", record_id=1, hours_ago=0,
                 title="A task"):
    """Insert a target record (so a title resolves) + an activity row."""
    with temp_app.app_context():
        sess = get_session()
        if table == "work_tasks" and sess.get(WorkTask, record_id) is None:
            sess.add(WorkTask(id=record_id, title=title, status="In Progress"))
        elif table == "feedback_items" and sess.get(FeedbackItem, record_id) is None:
            sess.add(FeedbackItem(id=record_id, title=title,
                                  feedback_type="Bug", status="New"))
        elif table == "personnel_issues" and sess.get(PersonnelIssue, record_id) is None:
            sess.add(PersonnelIssue(id=record_id, issue_description="x",
                                    status="Observed"))
        log = ActivityLog(
            table_name=table, record_id=record_id, action=action,
            field_name="status", old_value="In Progress", new_value=new_value,
            user_name="Hermes",
            created_at=datetime.utcnow() - timedelta(hours=hours_ago),
        )
        sess.add(log)
        sess.commit()
        return log.id


def test_changes_requires_bot_token(client, temp_app):
    assert client.get("/api/v1/changes").status_code == 401


def test_changes_window_returns_recent(client, temp_app, with_bot_token):
    _seed_change(temp_app, hours_ago=1, new_value="Complete")
    r = client.get("/api/v1/changes", headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["count"] == 1
    chg = body["changes"][0]
    assert chg["table"] == "work_tasks"
    assert chg["new_value"] == "Complete"
    assert chg["record_title"] == "A task"
    assert body["cursor"]["next_cursor"] == chg["id"]


def test_changes_window_excludes_old(client, temp_app, with_bot_token):
    _seed_change(temp_app, hours_ago=200)  # outside default 24h + max 168h
    r = client.get("/api/v1/changes?since_hours=24",
                   headers={"X-Token": BOT_TOKEN})
    assert r.get_json()["count"] == 0


def test_changes_cursor_only_newer(client, temp_app, with_bot_token):
    first = _seed_change(temp_app, record_id=1, hours_ago=2)
    second = _seed_change(temp_app, record_id=2, title="Second", hours_ago=1)
    r = client.get(f"/api/v1/changes?after_id={first}",
                   headers={"X-Token": BOT_TOKEN})
    body = r.get_json()
    ids = [c["id"] for c in body["changes"]]
    assert first not in ids and second in ids
    assert body["cursor"]["after_id"] == first


def test_changes_excludes_personnel(client, temp_app, with_bot_token):
    """Sensitive personnel_issues movement must never appear in the feed."""
    _seed_change(temp_app, table="personnel_issues", record_id=5, hours_ago=1)
    r = client.get("/api/v1/changes", headers={"X-Token": BOT_TOKEN})
    body = r.get_json()
    assert all(c["table"] != "personnel_issues" for c in body["changes"])
    assert "personnel_issues" not in body["tables"]


def test_changes_tables_filter(client, temp_app, with_bot_token):
    _seed_change(temp_app, table="work_tasks", record_id=1, hours_ago=1)
    _seed_change(temp_app, table="feedback_items", record_id=2,
                 title="A bug", new_value="Fixed", hours_ago=1)
    r = client.get("/api/v1/changes?tables=feedback_items",
                   headers={"X-Token": BOT_TOKEN})
    body = r.get_json()
    assert body["tables"] == ["feedback_items"]
    assert all(c["table"] == "feedback_items" for c in body["changes"])


def test_changes_bad_tables_falls_back_to_allowlist(client, temp_app, with_bot_token):
    """An unknown ?tables value must not silently return everything unfiltered
    nor expose a non-allowlisted table — it falls back to the full allow-list."""
    r = client.get("/api/v1/changes?tables=personnel_issues,bogus",
                   headers={"X-Token": BOT_TOKEN})
    body = r.get_json()
    assert "personnel_issues" not in body["tables"]
    assert "work_tasks" in body["tables"]


def test_changes_invalid_after_id(client, temp_app, with_bot_token):
    r = client.get("/api/v1/changes?after_id=abc",
                   headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 400


def test_changes_has_more_pagination(client, temp_app, with_bot_token):
    for i in range(1, 6):
        _seed_change(temp_app, record_id=i, title=f"t{i}", hours_ago=1)
    r = client.get("/api/v1/changes?limit=2", headers={"X-Token": BOT_TOKEN})
    body = r.get_json()
    assert body["count"] == 2
    assert body["cursor"]["has_more"] is True
    # advancing the cursor pulls the next page
    nxt = body["cursor"]["next_cursor"]
    r2 = client.get(f"/api/v1/changes?after_id={nxt}&limit=2",
                    headers={"X-Token": BOT_TOKEN})
    body2 = r2.get_json()
    assert body2["count"] == 2
    assert body2["changes"][0]["id"] > nxt
