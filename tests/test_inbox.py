"""In-process tests for the inbox blueprint."""
import pytest

from app.db import get_session
from app.models import ActivityLog, InboxItem, WorkTask


INBOX_TOKEN = "test-inbox-token"


@pytest.fixture
def with_token(monkeypatch):
    """Set a known inbox-scope token. Reload the module-level cache."""
    monkeypatch.setenv("TASKTRACK_TOKEN_INBOX", INBOX_TOKEN)
    # tokens.py reads at import time — re-load it for this test.
    import importlib
    from app import tokens
    importlib.reload(tokens)
    yield
    importlib.reload(tokens)


def _login(client):
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"


# ── POST capture ────────────────────────────────────────────────────────

def test_capture_requires_token(client):
    r = client.post("/api/v1/inbox", json={"title": "x"})
    assert r.status_code in (401, 503)


def test_capture_creates_inbox_item(client, with_token):
    r = client.post(
        "/api/v1/inbox",
        json={"title": "Pick up dry cleaning", "source": "mytrack-bot"},
        headers={"X-Token": INBOX_TOKEN},
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["title"] == "Pick up dry cleaning"
    assert body["source"] == "mytrack-bot"
    assert body["status"] == "New"
    assert body["promoted_to_id"] is None


def test_capture_dedupes_on_source_ref(client, with_token):
    payload = {"title": "Same item", "source": "paperless", "source_ref": "doc-42"}
    r1 = client.post("/api/v1/inbox", json=payload, headers={"X-Token": INBOX_TOKEN})
    assert r1.status_code == 201
    r2 = client.post("/api/v1/inbox", json=payload, headers={"X-Token": INBOX_TOKEN})
    # Second call returns existing row (200 instead of 201).
    assert r2.status_code == 200
    assert r1.get_json()["id"] == r2.get_json()["id"]


def test_capture_routes_directly_when_target_table_set(client, with_token, temp_app):
    r = client.post(
        "/api/v1/inbox",
        json={
            "title": "Hot CAD fix",
            "body": "Layer scheme broken on E-501",
            "source": "voice-memo",
            "target_table": "work_tasks",
            "priority": "High",
        },
        headers={"X-Token": INBOX_TOKEN},
    )
    assert r.status_code == 201
    body = r.get_json()
    assert body["routed_to"] == "work_tasks"
    record_id = body["record_id"]
    with temp_app.app_context():
        sess = get_session()
        wt = sess.get(WorkTask, record_id)
        assert wt is not None
        assert wt.title == "Hot CAD fix"
        assert wt.description == "Layer scheme broken on E-501"
        assert wt.priority == "High"
        # Inbox table stays empty when target_table is set.
        assert sess.query(InboxItem).count() == 0


def test_capture_rejects_unknown_target_table(client, with_token):
    r = client.post(
        "/api/v1/inbox",
        json={"title": "x", "target_table": "not_a_table"},
        headers={"X-Token": INBOX_TOKEN},
    )
    assert r.status_code == 400


def test_capture_rejects_self_targeting(client, with_token):
    r = client.post(
        "/api/v1/inbox",
        json={"title": "x", "target_table": "inbox_items"},
        headers={"X-Token": INBOX_TOKEN},
    )
    assert r.status_code == 400


def test_capture_requires_title(client, with_token):
    r = client.post("/api/v1/inbox", json={"body": "no title"},
                    headers={"X-Token": INBOX_TOKEN})
    assert r.status_code == 400


# ── GET / list ──────────────────────────────────────────────────────────

def test_list_requires_login(client):
    r = client.get("/api/v1/inbox")
    assert r.status_code in (401, 302)


def test_list_returns_non_archived_by_default(client, with_token, temp_app):
    # Seed three items: one Archived, two New.
    for s in ("Archived", "New", "New"):
        client.post("/api/v1/inbox",
                    json={"title": f"item {s}", "source": "test"},
                    headers={"X-Token": INBOX_TOKEN})
    with temp_app.app_context():
        sess = get_session()
        sess.query(InboxItem).filter_by(title="item Archived").update({"status": "Archived"})
        sess.commit()
    _login(client)
    r = client.get("/api/v1/inbox")
    assert r.status_code == 200
    items = r.get_json()
    assert len(items) == 2
    assert all(i["status"] != "Archived" for i in items)


# ── PATCH ───────────────────────────────────────────────────────────────

def test_patch_updates_status_and_completes_on_done(client, with_token, temp_app):
    client.post("/api/v1/inbox",
                json={"title": "do laundry", "source": "test"},
                headers={"X-Token": INBOX_TOKEN})
    with temp_app.app_context():
        sess = get_session()
        item_id = sess.query(InboxItem).first().id
    _login(client)
    r = client.patch(f"/api/v1/inbox/{item_id}", json={"status": "Done"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "Done"
    assert body["completed_at"] is not None


def test_patch_ignores_unknown_fields(client, with_token, temp_app):
    client.post("/api/v1/inbox", json={"title": "x", "source": "t"},
                headers={"X-Token": INBOX_TOKEN})
    with temp_app.app_context():
        sess = get_session()
        item_id = sess.query(InboxItem).first().id
    _login(client)
    r = client.patch(f"/api/v1/inbox/{item_id}",
                     json={"id": 999, "status": "In Progress"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "In Progress"


# ── Promote ─────────────────────────────────────────────────────────────

def test_promote_creates_target_record_and_archives_inbox(client, with_token, temp_app):
    client.post("/api/v1/inbox",
                json={"title": "Layer scheme broken", "body": "see drawing E-501",
                      "source": "voice", "priority": "High"},
                headers={"X-Token": INBOX_TOKEN})
    with temp_app.app_context():
        sess = get_session()
        item_id = sess.query(InboxItem).first().id
    _login(client)
    r = client.post(f"/api/v1/inbox/{item_id}/promote",
                    json={"target_table": "work_tasks"})
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["promoted_to"]["table"] == "work_tasks"
    new_id = body["promoted_to"]["id"]
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.status == "Archived"
        assert item.promoted_to_table == "work_tasks"
        assert item.promoted_to_id == new_id
        wt = sess.get(WorkTask, new_id)
        assert wt.title == "Layer scheme broken"
        assert wt.priority == "High"
        assert wt.description == "see drawing E-501"


def test_promote_rejects_self(client, with_token, temp_app):
    client.post("/api/v1/inbox", json={"title": "x", "source": "t"},
                headers={"X-Token": INBOX_TOKEN})
    with temp_app.app_context():
        sess = get_session()
        item_id = sess.query(InboxItem).first().id
    _login(client)
    r = client.post(f"/api/v1/inbox/{item_id}/promote",
                    json={"target_table": "inbox_items"})
    assert r.status_code == 400


# ── Delete ──────────────────────────────────────────────────────────────

def test_delete_removes_row(client, with_token, temp_app):
    client.post("/api/v1/inbox", json={"title": "x", "source": "t"},
                headers={"X-Token": INBOX_TOKEN})
    with temp_app.app_context():
        sess = get_session()
        item_id = sess.query(InboxItem).first().id
    _login(client)
    r = client.delete(f"/api/v1/inbox/{item_id}")
    assert r.status_code == 204
    with temp_app.app_context():
        sess = get_session()
        assert sess.get(InboxItem, item_id) is None
