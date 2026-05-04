"""In-process tests for the attachments blueprint.

The MinIO/boto3 layer is monkeypatched: we never touch a real network or
container. Tests focus on the request/response contract, validation, and
that the DB row + activity_log entries land where expected.
"""
import io

import pytest

from app.db import get_session
from app.models import ActivityLog, Attachment, WorkTask
from app.services import attachments as att_svc


# ── Stub MinIO ────────────────────────────────────────────────────────────

class _FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, ContentLength, Metadata=None):
        self.objects[(Bucket, Key)] = {"body": Body, "type": ContentType}
        return {"ETag": '"stub"'}

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


# ── Helpers ───────────────────────────────────────────────────────────────

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


# ── Tests ─────────────────────────────────────────────────────────────────

def test_list_requires_auth(client):
    r = client.get("/api/v1/attachments/work_tasks/1")
    assert r.status_code in (401, 302)


def test_list_rejects_unknown_table(client):
    _login(client)
    r = client.get("/api/v1/attachments/nope/1")
    assert r.status_code == 400


def test_list_404_when_record_missing(client):
    _login(client)
    r = client.get("/api/v1/attachments/work_tasks/999")
    assert r.status_code == 404


def test_upload_list_download_delete_roundtrip(client, temp_app, patched_minio):
    _login(client)
    record_id = _seed_work_task(temp_app)

    # Upload a tiny PDF.
    payload = {"file": (io.BytesIO(b"%PDF-1.4 stub"), "drawing.pdf", "application/pdf")}
    r = client.post(
        f"/api/v1/attachments/work_tasks/{record_id}",
        data=payload,
        content_type="multipart/form-data",
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["filename"] == "drawing.pdf"
    assert body["size_bytes"] == len(b"%PDF-1.4 stub")
    assert body["sha256"]
    assert body["download_url"].startswith("/api/v1/attachments/")
    att_id = body["id"]

    # MinIO was hit once.
    assert len(patched_minio.objects) == 1
    bucket, key = next(iter(patched_minio.objects))
    assert key.startswith(f"work_tasks/{record_id}/")

    # List returns the row.
    r = client.get(f"/api/v1/attachments/work_tasks/{record_id}")
    assert r.status_code == 200
    assert len(r.get_json()) == 1

    # Download redirects to the presigned URL.
    r = client.get(f"/api/v1/attachments/{att_id}/download", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].startswith("http://stub-minio/")

    # Activity log got an attachment_added row.
    with temp_app.app_context():
        sess = get_session()
        actions = [a.action for a in sess.query(ActivityLog).all()]
        assert "attachment_added" in actions

    # Delete returns 204 and clears the row + the bucket entry.
    r = client.delete(f"/api/v1/attachments/{att_id}")
    assert r.status_code == 204
    with temp_app.app_context():
        sess = get_session()
        assert sess.get(Attachment, att_id) is None
        assert "attachment_removed" in [a.action for a in sess.query(ActivityLog).all()]
    assert len(patched_minio.objects) == 0


def test_upload_dedupe_returns_existing(client, temp_app, patched_minio):
    _login(client)
    record_id = _seed_work_task(temp_app)

    body = b"%PDF-1.4 same-bytes"
    for _ in range(2):
        r = client.post(
            f"/api/v1/attachments/work_tasks/{record_id}",
            data={"file": (io.BytesIO(body), "drawing.pdf", "application/pdf")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 201

    with temp_app.app_context():
        sess = get_session()
        rows = sess.query(Attachment).all()
        assert len(rows) == 1


def test_upload_rejects_bad_extension(client, temp_app, patched_minio):
    _login(client)
    record_id = _seed_work_task(temp_app)
    r = client.post(
        f"/api/v1/attachments/work_tasks/{record_id}",
        data={"file": (io.BytesIO(b"#!/bin/sh"), "evil.sh", "text/x-shellscript")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert "not allowed" in r.get_json()["error"]


def test_upload_oversized_blocked_by_service(client, temp_app, patched_minio, monkeypatch):
    _login(client)
    record_id = _seed_work_task(temp_app)
    # Drop the cap to 100 bytes to keep the test fast — but raise the
    # Werkzeug outer cap higher so we exercise the service-layer check.
    monkeypatch.setenv("ATTACHMENT_MAX_BYTES", "100")
    blob = b"x" * 1024
    r = client.post(
        f"/api/v1/attachments/work_tasks/{record_id}",
        data={"file": (io.BytesIO(b"%PDF-1.4" + blob), "big.pdf", "application/pdf")},
        content_type="multipart/form-data",
    )
    # Service layer enforces 100-byte cap → 400. Werkzeug's outer cap
    # (still 50 MB on the running app) doesn't fire here.
    assert r.status_code == 400
    assert "limit" in r.get_json()["error"].lower()


def test_unauthenticated_upload_blocked(client, temp_app):
    record_id = _seed_work_task(temp_app)
    r = client.post(
        f"/api/v1/attachments/work_tasks/{record_id}",
        data={"file": (io.BytesIO(b"hi"), "x.pdf", "application/pdf")},
        content_type="multipart/form-data",
    )
    assert r.status_code in (401, 302)
