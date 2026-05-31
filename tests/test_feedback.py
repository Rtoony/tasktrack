"""Feedback capture loop tests."""
import io
import json

import pytest

from app.db import get_session
from app.models import Attachment, FeedbackItem
from app.services import attachments as att_svc


class _FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, ContentLength, Metadata=None):
        self.objects[(Bucket, Key)] = {"body": Body, "type": ContentType}
        return {"ETag": "stub"}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"http://stub-minio/{Params['Bucket']}/{Params['Key']}?sig=stub"


@pytest.fixture
def patched_minio(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setenv("MINIO_ACCESS_KEY", "stub")
    monkeypatch.setenv("MINIO_SECRET_KEY", "stub")
    monkeypatch.setattr(att_svc, "_client", lambda: fake)
    return fake


def test_feedback_page_requires_auth(client):
    res = client.get("/feedback")
    assert res.status_code in (302, 401)


def test_feedback_page_renders_management_contract(auth_client):
    res = auth_client.get("/feedback")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Feedback inbox" in html
    assert "/api/v1/feedback_items" in html
    assert "/api/v1/attachments/feedback_items/" in html
    assert "Codex context loop" in html


def test_app_context_endpoint_requires_auth(client):
    assert client.get("/api/v1/app-context").status_code in (302, 401)


def test_app_context_endpoint_returns_build_context(auth_client):
    res = auth_client.get("/api/v1/app-context")
    assert res.status_code == 200
    body = res.get_json()
    assert body["app"] == "tasktrack"
    assert body["brand"]
    assert "server_time" in body
    assert "request_id" in body
    assert set(body["git"]) >= {"commit", "short_commit", "branch", "dirty"}
    assert body["runtime"]["db_name"]


def test_shell_includes_feedback_widget_and_left_nav(auth_client):
    res = auth_client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "openFeedbackModal" in html
    assert "feedback_items" in html
    assert 'href="/feedback"' in html
    assert "Captured context" in html
    assert "feedback_context_version: 2" in html
    assert "recent_telemetry" in html
    assert "collectFeedbackUiState" in html
    assert "loadFeedbackAppContext" in html
    assert "/api/v1/app-context" in html


def test_feedback_api_create_and_update(auth_client, temp_app):
    payload = {
        "title": "Sidebar wording is confusing",
        "body": "The left rail label does not match the Claude Design example.",
        "feedback_type": "Copy",
        "priority": "High",
        "page_url": "http://localhost/?tab=dashboard",
        "tab": "dashboard",
        "component_label": "left rail",
        "context_json": json.dumps({"tab": "dashboard", "viewport": {"width": 1440}}),
        "tags": "testing, sidebar",
        "source": "in-app",
    }
    res = auth_client.post("/api/v1/feedback_items", json=payload)
    assert res.status_code == 201, res.data
    row = res.get_json()
    assert row["id"]
    assert row["status"] == "New"
    assert row["created_by_name"] == "Tester"

    update = {"status": "Fixed", "resolution_notes": "Adjusted left rail copy."}
    res = auth_client.put(f"/api/v1/feedback_items/{row['id']}", json=update)
    assert res.status_code == 200, res.data
    updated = res.get_json()
    assert updated["status"] == "Fixed"
    assert updated["resolution_notes"] == "Adjusted left rail copy."

    with temp_app.app_context():
        sess = get_session()
        saved = sess.get(FeedbackItem, row["id"])
        assert saved is not None
        assert saved.title == "Sidebar wording is confusing"
        assert json.loads(saved.context_json)["tab"] == "dashboard"


def test_feedback_rejects_bad_context_json(auth_client):
    res = auth_client.post("/api/v1/feedback_items", json={
        "title": "Bad context",
        "context_json": "{not-json",
    })
    assert res.status_code == 400
    assert "context_json" in res.get_json()["error"]


def test_feedback_screenshot_attachment_roundtrip(auth_client, temp_app, patched_minio):
    res = auth_client.post("/api/v1/feedback_items", json={
        "title": "Screenshot-backed note",
        "body": "Button alignment is off.",
    })
    assert res.status_code == 201
    feedback_id = res.get_json()["id"]

    res = auth_client.post(
        f"/api/v1/attachments/feedback_items/{feedback_id}",
        data={"file": (io.BytesIO(b"\x89PNG\r\n\x1a\nsmall"), "screen.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 201, res.data
    body = res.get_json()
    assert body["filename"] == "screen.png"
    assert body["download_url"].startswith("/api/v1/attachments/")
    assert len(patched_minio.objects) == 1

    res = auth_client.get(f"/api/v1/attachments/feedback_items/{feedback_id}")
    assert res.status_code == 200
    assert len(res.get_json()) == 1

    with temp_app.app_context():
        sess = get_session()
        assert sess.query(Attachment).filter_by(table_name="feedback_items", record_id=feedback_id).count() == 1
