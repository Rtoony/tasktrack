"""In-process tests for the bot-scoped /api/v1/comment/<table>/<id> write-back."""
import importlib

import pytest
from sqlalchemy import select

from app.db import get_session
from app.models import ActivityLog, Comment, FeedbackItem, PersonnelIssue, WorkTask

BOT_TOKEN = "test-bot-token"


@pytest.fixture
def with_bot_token(monkeypatch):
    monkeypatch.setenv("TASKTRACK_TOKEN_BOT", BOT_TOKEN)
    from app import tokens
    importlib.reload(tokens)
    yield
    importlib.reload(tokens)


def _seed_task(temp_app):
    with temp_app.app_context():
        sess = get_session()
        t = WorkTask(title="Work it", status="In Progress")
        sess.add(t)
        sess.commit()
        return t.id


def _seed_feedback(temp_app):
    with temp_app.app_context():
        sess = get_session()
        f = FeedbackItem(title="A bug", feedback_type="Bug", status="New")
        sess.add(f)
        sess.commit()
        return f.id


def _seed_personnel(temp_app):
    with temp_app.app_context():
        sess = get_session()
        p = PersonnelIssue(issue_description="x", status="Observed")
        sess.add(p)
        sess.commit()
        return p.id


def test_comment_requires_bot_token(client, temp_app):
    tid = _seed_task(temp_app)
    assert client.post(f"/api/v1/comment/work_tasks/{tid}",
                       json={"body": "hi"}).status_code == 401


def test_comment_added_and_attributed(client, temp_app, with_bot_token):
    tid = _seed_task(temp_app)
    r = client.post(f"/api/v1/comment/work_tasks/{tid}",
                    json={"body": "Started the fix; deployed to lab."},
                    headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["ok"] is True
    assert body["comment"]["user_name"] == "Hermes"
    with temp_app.app_context():
        sess = get_session()
        c = sess.scalars(
            select(Comment).where(Comment.record_id == tid)).first()
        assert c is not None and c.user_name == "Hermes"
        # activity-log row written so it flows back through /api/v1/changes
        act = sess.scalars(
            select(ActivityLog).where(ActivityLog.record_id == tid,
                                      ActivityLog.action == "comment")).first()
        assert act is not None and act.user_name == "Hermes"


def test_comment_on_feedback(client, temp_app, with_bot_token):
    fid = _seed_feedback(temp_app)
    r = client.post(f"/api/v1/comment/feedback_items/{fid}",
                    json={"body": "Looking into this."},
                    headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 201


def test_comment_ai_dev_audience_kept(client, temp_app, with_bot_token):
    tid = _seed_task(temp_app)
    r = client.post(f"/api/v1/comment/work_tasks/{tid}",
                    json={"body": "note", "audience": "ai-dev"},
                    headers={"X-Token": BOT_TOKEN})
    assert r.get_json()["comment"]["audience"] == "ai-dev"


def test_comment_unknown_audience_clamped(client, temp_app, with_bot_token):
    tid = _seed_task(temp_app)
    r = client.post(f"/api/v1/comment/work_tasks/{tid}",
                    json={"body": "note", "audience": "root"},
                    headers={"X-Token": BOT_TOKEN})
    assert r.get_json()["comment"]["audience"] == ""


def test_comment_empty_body_rejected(client, temp_app, with_bot_token):
    tid = _seed_task(temp_app)
    r = client.post(f"/api/v1/comment/work_tasks/{tid}",
                    json={"body": "   "}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 400


def test_comment_table_not_allowed(client, temp_app, with_bot_token):
    """Hermes must not be able to comment on sensitive personnel rows."""
    pid = _seed_personnel(temp_app)
    r = client.post(f"/api/v1/comment/personnel_issues/{pid}",
                    json={"body": "x"}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 400


def test_comment_missing_record_404(client, temp_app, with_bot_token):
    r = client.post("/api/v1/comment/work_tasks/999999",
                    json={"body": "x"}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 404
