"""In-process tests for links blueprint + smart-link recognizer."""
import pytest

from app.db import get_session
from app.models import ActivityLog, Link, WorkTask
from app.services import links as link_svc


def _login(client):
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"


def _seed_work_task(temp_app, title="Seed task") -> int:
    with temp_app.app_context():
        sess = get_session()
        wt = WorkTask(title=title)
        sess.add(wt)
        sess.commit()
        return wt.id


# ── Recognizer unit tests (pure, no Flask) ──────────────────────────────

@pytest.mark.parametrize("url,expected_kind,expected_label_contains", [
    ("https://paperless.roonytoony.dev/documents/42", "paperless", "Paperless doc #42"),
    ("https://portal.roonytoony.dev/calendar", "calendar", "Calendar"),
    ("https://portal.roonytoony.dev/calendar/2026-05-04", "calendar", "2026-05-04"),
    ("https://portal.roonytoony.dev/dashboard", "portal", "dashboard"),
    ("https://github.com/Rtoony/tasktrack/pull/12", "github_pr", "PR Rtoony/tasktrack#12"),
    ("https://github.com/Rtoony/tasktrack/issues/7", "github_issue", "Issue Rtoony/tasktrack#7"),
    ("https://github.com/Rtoony/tasktrack", "github_repo", "Rtoony/tasktrack"),
    ("https://t.me/MyTrack_Tasks_Bot/123", "telegram", "@MyTrack_Tasks_Bot msg 123"),
    ("https://example.com/foo/bar", "generic", "example.com/bar"),
])
def test_recognizer_labels_known_hosts(url, expected_kind, expected_label_contains):
    rec = link_svc._recognize(url)
    assert rec.source_kind == expected_kind
    assert expected_label_contains in rec.label


# ── Route tests ─────────────────────────────────────────────────────────

def test_list_requires_auth(client):
    r = client.get("/api/v1/links/work_tasks/1")
    assert r.status_code in (401, 302)


def test_list_rejects_unknown_table(client):
    _login(client)
    r = client.get("/api/v1/links/nope/1")
    assert r.status_code == 400


def test_list_404_when_record_missing(client):
    _login(client)
    r = client.get("/api/v1/links/work_tasks/999")
    assert r.status_code == 404


def test_add_list_delete_roundtrip(client, temp_app):
    _login(client)
    record_id = _seed_work_task(temp_app)

    # Add a Paperless link — recognizer should label it.
    r = client.post(
        f"/api/v1/links/work_tasks/{record_id}",
        json={"url": "https://paperless.roonytoony.dev/documents/42"},
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["source_kind"] == "paperless"
    assert body["label"] == "Paperless doc #42"
    link_id = body["id"]

    # List shows it.
    r = client.get(f"/api/v1/links/work_tasks/{record_id}")
    assert len(r.get_json()) == 1

    # Activity log got an entry.
    with temp_app.app_context():
        sess = get_session()
        actions = [a.action for a in sess.query(ActivityLog).all()]
        assert "link_added" in actions

    # Delete returns 204; row is gone.
    r = client.delete(f"/api/v1/links/{link_id}")
    assert r.status_code == 204
    with temp_app.app_context():
        sess = get_session()
        assert sess.get(Link, link_id) is None
        assert "link_removed" in [a.action for a in sess.query(ActivityLog).all()]


def test_add_dedupes_same_url(client, temp_app):
    _login(client)
    record_id = _seed_work_task(temp_app)
    url = "https://github.com/Rtoony/tasktrack/pull/1"
    for _ in range(3):
        r = client.post(f"/api/v1/links/work_tasks/{record_id}", json={"url": url})
        assert r.status_code == 201
    with temp_app.app_context():
        sess = get_session()
        assert sess.query(Link).count() == 1


def test_user_label_overrides_recognizer(client, temp_app):
    _login(client)
    record_id = _seed_work_task(temp_app)
    r = client.post(
        f"/api/v1/links/work_tasks/{record_id}",
        json={"url": "https://paperless.roonytoony.dev/documents/9", "label": "Survey markup"},
    )
    assert r.status_code == 201
    body = r.get_json()
    assert body["label"] == "Survey markup"
    assert body["source_kind"] == "paperless"  # recognizer still tags the kind


@pytest.mark.parametrize("bad_url,reason", [
    ("", "required"),
    ("not-a-url", "http"),
    ("ftp://example.com", "http"),
    ("https://", "host"),
    ("https://" + "x" * 3000, "2048"),
])
def test_add_rejects_bad_urls(client, temp_app, bad_url, reason):
    _login(client)
    record_id = _seed_work_task(temp_app)
    r = client.post(f"/api/v1/links/work_tasks/{record_id}", json={"url": bad_url})
    assert r.status_code == 400
    assert reason.lower() in r.get_json()["error"].lower()
