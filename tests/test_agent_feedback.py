"""In-process tests for the bot-scoped feedback triage + status endpoints."""
import importlib

import pytest
from sqlalchemy import select

from app.db import get_session
from app.models import ActivityLog, FeedbackItem

BOT_TOKEN = "test-bot-token"


@pytest.fixture
def with_bot_token(monkeypatch):
    monkeypatch.setenv("TASKTRACK_TOKEN_BOT", BOT_TOKEN)
    from app import tokens
    importlib.reload(tokens)
    yield
    importlib.reload(tokens)


def _seed(temp_app, **kw):
    with temp_app.app_context():
        sess = get_session()
        f = FeedbackItem(
            title=kw.get("title", "Button misaligned on Today view"),
            feedback_type=kw.get("feedback_type", "Bug"),
            priority=kw.get("priority", "Medium"),
            status=kw.get("status", "New"),
            page_url=kw.get("page_url", "/today"),
            component_label=kw.get("component_label", "overdue-list"),
        )
        sess.add(f)
        sess.commit()
        return f.id


# ── reads ────────────────────────────────────────────────────────────────────
def test_list_requires_bot_token(client):
    assert client.get("/api/v1/feedback").status_code == 401


def test_ai_instructions_requires_bot_token(client):
    assert client.get("/api/v1/ai-instructions").status_code == 401


def test_ai_instructions_skips_archived_parent(client, temp_app, with_bot_token):
    # #42 (pre-merge): an AI-dev comment on an ARCHIVED parent task is stale — the
    # feed Hermes pulls must drop it but keep instructions on active records.
    from datetime import datetime
    from app.models import Comment, WorkTask
    with temp_app.app_context():
        sess = get_session()
        active = WorkTask(title="active parent")
        archived = WorkTask(title="archived parent", archived_at=datetime(2026, 1, 1, 12, 0, 0))
        sess.add(active)
        sess.add(archived)
        sess.commit()
        sess.add(Comment(table_name="work_tasks", record_id=active.id, user_name="Josh",
                         body="build the active thing", audience="ai-dev"))
        sess.add(Comment(table_name="work_tasks", record_id=archived.id, user_name="Josh",
                         body="build the archived thing", audience="ai-dev"))
        sess.commit()
    body = client.get("/api/v1/ai-instructions", headers={"X-Token": BOT_TOKEN}).get_json()
    bodies = [i["body"] for i in body["instructions"]]
    assert "build the active thing" in bodies
    assert "build the archived thing" not in bodies


def test_ai_instructions_lists_only_ai_dev_comments(client, temp_app, with_bot_token):
    # feedback #42: the bot pulls only audience='ai-dev' comments (not normal ones).
    from app.models import Comment
    with temp_app.app_context():
        sess = get_session()
        sess.add(Comment(table_name="work_tasks", record_id=7, user_name="Josh",
                         body="build the CSV export", audience="ai-dev"))
        sess.add(Comment(table_name="work_tasks", record_id=7, user_name="Josh",
                         body="just a human note", audience=""))
        sess.commit()
    r = client.get("/api/v1/ai-instructions", headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    body = r.get_json()
    bodies = [i["body"] for i in body["instructions"]]
    assert "build the CSV export" in bodies
    assert "just a human note" not in bodies
    assert body["count"] == 1
    assert body["instructions"][0]["record_url"] == "/?tab=work_tasks&record=7"


def test_list_open_excludes_terminal(client, temp_app, with_bot_token):
    open_id = _seed(temp_app, status="New")
    _seed(temp_app, status="Accepted")  # terminal → excluded from the default open view
    r = client.get("/api/v1/feedback", headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    body = r.get_json()
    ids = [i["id"] for i in body["items"]]
    assert open_id in ids
    # localization fields are present so the agent can find the code
    item = next(i for i in body["items"] if i["id"] == open_id)
    assert item["page_url"] == "/today" and item["component_label"] == "overdue-list"
    assert body["counts"]["open"] >= 1 and "by_type" in body["counts"]


def test_list_filter_by_type(client, temp_app, with_bot_token):
    _seed(temp_app, feedback_type="Idea", status="New")
    _seed(temp_app, feedback_type="Bug", status="New")
    r = client.get("/api/v1/feedback?type=Idea&status=all", headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200
    assert r.get_json()["items"]
    assert all(i["feedback_type"] == "Idea" for i in r.get_json()["items"])


# ── gated writes ─────────────────────────────────────────────────────────────
def test_status_requires_bot_token(client, temp_app):
    fid = _seed(temp_app)
    assert client.post(f"/api/v1/feedback/{fid}/status").status_code == 401


def test_status_defaults_to_fixed_non_terminal(client, temp_app, with_bot_token):
    fid = _seed(temp_app, status="New")
    r = client.post(f"/api/v1/feedback/{fid}/status", json={}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    assert r.get_json()["to"] == "Fixed"
    with temp_app.app_context():
        sess = get_session()
        row = sess.get(FeedbackItem, fid)
        assert row.status == "Fixed"
        assert row.completed_at is None  # Fixed is NOT terminal — Josh still accepts
        act = sess.scalars(select(ActivityLog).where(
            ActivityLog.table_name == "feedback_items",
            ActivityLog.record_id == fid,
            ActivityLog.action == "status_change")).first()
        assert act is not None and act.user_name == "Hermes" and act.new_value == "Fixed"


def test_terminal_status_sets_completed_at(client, temp_app, with_bot_token):
    fid = _seed(temp_app, status="Fixed")
    r = client.post(f"/api/v1/feedback/{fid}/status",
                    json={"status": "Accepted"}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200
    with temp_app.app_context():
        assert get_session().get(FeedbackItem, fid).completed_at is not None


def test_bot_reopen_clears_completed_at(client, temp_app, with_bot_token):
    # audit #26 on the BOT path: reopening a terminal item to a non-done status
    # must clear completed_at (the SPA path is covered in test_feedback; this isn't).
    fid = _seed(temp_app, status="Fixed")
    client.post(f"/api/v1/feedback/{fid}/status", json={"status": "Accepted"}, headers={"X-Token": BOT_TOKEN})
    with temp_app.app_context():
        assert get_session().get(FeedbackItem, fid).completed_at is not None
    r = client.post(f"/api/v1/feedback/{fid}/status", json={"status": "Triaged"}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200
    with temp_app.app_context():
        assert get_session().get(FeedbackItem, fid).completed_at is None


def test_status_with_resolution_notes_logged(client, temp_app, with_bot_token):
    fid = _seed(temp_app, status="New")
    r = client.post(f"/api/v1/feedback/{fid}/status",
                    json={"status": "Triaged", "resolution_notes": "scoped as a dev-job"},
                    headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200
    assert r.get_json()["to"] == "Triaged"
    with temp_app.app_context():
        sess = get_session()
        assert sess.get(FeedbackItem, fid).resolution_notes == "scoped as a dev-job"
        acts = [a.field_name for a in sess.scalars(select(ActivityLog).where(
            ActivityLog.table_name == "feedback_items", ActivityLog.record_id == fid)).all()]
        assert "resolution_notes" in acts


def test_invalid_status_rejected(client, temp_app, with_bot_token):
    fid = _seed(temp_app)
    r = client.post(f"/api/v1/feedback/{fid}/status",
                    json={"status": "Bogus"}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 400


def test_status_not_found(client, temp_app, with_bot_token):
    r = client.post("/api/v1/feedback/999999/status",
                    json={"status": "Fixed"}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 404
