"""In-process tests for the bot-scoped /api/v1/task/<table>/<id>/status endpoint."""
import importlib

import pytest
from sqlalchemy import select

from app.db import get_session
from app.models import ActivityLog, WorkTask

BOT_TOKEN = "test-bot-token"


@pytest.fixture
def with_bot_token(monkeypatch):
    monkeypatch.setenv("TASKTRACK_TOKEN_BOT", BOT_TOKEN)
    from app import tokens
    importlib.reload(tokens)
    yield
    importlib.reload(tokens)


def _seed_task(temp_app, status="In Progress"):
    with temp_app.app_context():
        sess = get_session()
        t = WorkTask(title="Close me", status=status, priority="High")
        sess.add(t)
        sess.commit()
        return t.id


def test_status_requires_bot_token(client, temp_app):
    tid = _seed_task(temp_app)
    assert client.post(f"/api/v1/task/work_tasks/{tid}/status").status_code == 401


def test_close_defaults_to_complete(client, temp_app, with_bot_token):
    tid = _seed_task(temp_app)
    r = client.post(f"/api/v1/task/work_tasks/{tid}/status",
                    json={}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["to"] == "Complete"
    # status actually changed + an activity row attributed to Hermes was written
    with temp_app.app_context():
        sess = get_session()
        assert sess.get(WorkTask, tid).status == "Complete"
        act = sess.scalars(
            select(ActivityLog).where(ActivityLog.record_id == tid,
                                       ActivityLog.action == "status_change")
        ).first()
        assert act is not None and act.user_name == "Hermes"
        assert act.new_value == "Complete"


def test_explicit_valid_status(client, temp_app, with_bot_token):
    tid = _seed_task(temp_app, status="Not Started")
    r = client.post(f"/api/v1/task/work_tasks/{tid}/status",
                    json={"status": "On Hold"}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200
    assert r.get_json()["to"] == "On Hold"


def test_invalid_status_rejected(client, temp_app, with_bot_token):
    tid = _seed_task(temp_app)
    r = client.post(f"/api/v1/task/work_tasks/{tid}/status",
                    json={"status": "Bogus"}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 400


def test_non_task_table_rejected(client, temp_app, with_bot_token):
    r = client.post("/api/v1/task/calendar_events/1/status",
                    json={}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 400


def test_missing_task_404(client, temp_app, with_bot_token):
    r = client.post("/api/v1/task/work_tasks/999999/status",
                    json={}, headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 404
