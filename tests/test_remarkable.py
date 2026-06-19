"""Tests for the reMarkable bridge (#55).

Three layers, none of which touch the real tablet or MinIO:
  * pure functions (_classify, attachment_to_pdf_bytes) — no I/O at all
  * the service orchestration (send_attachment) with a fake RmCloud + fake bytes
  * the route contract (auth, 404, status-code passthrough) with send_attachment stubbed

The live upload to rmfakecloud is deliberately never exercised here — pushing a
real doc to RToony's tablet is a visible-artifact action, not test scaffolding.
"""
import io

import pytest

from app.db import get_session
from app.models import ActivityLog, Attachment, WorkTask
from app.services import attachments as att_svc
from app.services import remarkable as rm_svc


def _png_bytes() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(out, format="PNG")
    return out.getvalue()


def _att(**kw) -> Attachment:
    base = dict(table_name="work_tasks", record_id=1, object_key="k",
                filename="shot.png", content_type="image/png", size_bytes=10,
                sha256="deadbeef")
    base.update(kw)
    return Attachment(**base)


# ── _classify ──────────────────────────────────────────────────────────────

def test_classify_pdf_by_mime_and_ext():
    assert rm_svc._classify(_att(filename="a.pdf", content_type="application/pdf")) == "pdf"
    assert rm_svc._classify(_att(filename="a.pdf", content_type="")) == "pdf"


def test_classify_image():
    assert rm_svc._classify(_att(filename="a.png", content_type="image/png")) == "image"
    assert rm_svc._classify(_att(filename="a.jpg", content_type="image/jpeg")) == "image"


def test_classify_rejects_unrenderable_types():
    for fn, ct in [("plan.dwg", "application/octet-stream"),
                   ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                   ("doc.docx", "application/msword")]:
        with pytest.raises(rm_svc.RemarkableError) as ei:
            rm_svc._classify(_att(filename=fn, content_type=ct))
        assert ei.value.status_code == 415


# ── attachment_to_pdf_bytes ─────────────────────────────────────────────────

def test_pdf_passthrough_keeps_bytes():
    blob = b"%PDF-1.4\n stub pdf body"
    assert rm_svc.attachment_to_pdf_bytes(blob, "pdf") == blob


def test_pdf_passthrough_rejects_non_pdf():
    with pytest.raises(rm_svc.RemarkableError) as ei:
        rm_svc.attachment_to_pdf_bytes(b"GIF89a not a pdf", "pdf")
    assert ei.value.status_code == 422


def test_image_renders_to_pdf():
    pdf = rm_svc.attachment_to_pdf_bytes(_png_bytes(), "image")
    assert pdf.startswith(b"%PDF-")        # a real PDF, not the raw PNG
    assert len(pdf) > 100


def test_image_render_rejects_garbage():
    with pytest.raises(rm_svc.RemarkableError) as ei:
        rm_svc.attachment_to_pdf_bytes(b"not an image at all", "image")
    assert ei.value.status_code == 422


# ── _load_rmcloud degradation ───────────────────────────────────────────────

def test_load_rmcloud_missing_path_is_clean_503(monkeypatch):
    monkeypatch.setenv("TASKTRACK_RMCLOUD_PATH", "/nonexistent/inkpress/path")
    # Make sure a previously-imported rmcloud doesn't satisfy the import.
    monkeypatch.setitem(__import__("sys").modules, "rmcloud", None)
    with pytest.raises(rm_svc.RemarkableError) as ei:
        rm_svc._load_rmcloud()
    assert ei.value.status_code == 503


# ── send_attachment orchestration (fake client + fake bytes) ────────────────

class _FakeRmCloud:
    last = {}

    def login(self):
        return self

    def ensure_folder(self, name, parent=""):
        _FakeRmCloud.last["folder"] = name
        return "folder-id-123"

    def upload(self, pdf_path, name=None, parent=""):
        _FakeRmCloud.last["name"] = name
        _FakeRmCloud.last["parent"] = parent
        return {"id": "doc-999", "name": name, "parent": parent, "moved": True}


def test_send_attachment_converts_uploads_and_audits(temp_app, monkeypatch):
    # fake the client + the MinIO fetch so nothing real is touched
    monkeypatch.setattr(rm_svc, "_load_rmcloud",
                        lambda: (_FakeRmCloud, RuntimeError))
    monkeypatch.setattr(att_svc, "download_bytes", lambda att: _png_bytes())

    with temp_app.app_context():
        sess = get_session()
        wt = WorkTask(title="needs a sketch")
        sess.add(wt)
        sess.commit()
        att = Attachment(table_name="work_tasks", record_id=wt.id, object_key="k",
                         filename="redline.png", content_type="image/png",
                         size_bytes=10, sha256="abc123")
        sess.add(att)
        sess.commit()

        result = rm_svc.send_attachment(sess, att)
        sess.commit()

        assert result["ok"] is True
        assert result["doc_id"] == "doc-999"
        assert result["folder"] == rm_svc.REMARKABLE_FOLDER
        assert _FakeRmCloud.last["folder"] == rm_svc.REMARKABLE_FOLDER
        assert _FakeRmCloud.last["parent"] == "folder-id-123"
        # auditable like any other mutation
        logged = sess.query(ActivityLog).filter_by(
            table_name="work_tasks", record_id=wt.id,
            action="sent_to_remarkable").count()
        assert logged == 1


# ── route contract ──────────────────────────────────────────────────────────

def _login(client):
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"


def _seed_attachment(temp_app) -> int:
    with temp_app.app_context():
        sess = get_session()
        wt = WorkTask(title="route seed")
        sess.add(wt)
        sess.commit()
        att = Attachment(table_name="work_tasks", record_id=wt.id, object_key="k",
                         filename="x.png", content_type="image/png",
                         size_bytes=10, sha256="routehash")
        sess.add(att)
        sess.commit()
        return att.id


def test_remarkable_route_requires_auth(client):
    r = client.post("/api/v1/attachments/1/remarkable")
    assert r.status_code in (401, 302)


def test_remarkable_route_404_for_missing(client):
    _login(client)
    r = client.post("/api/v1/attachments/999999/remarkable")
    assert r.status_code == 404


def test_remarkable_route_happy_path(client, temp_app, monkeypatch):
    _login(client)
    att_id = _seed_attachment(temp_app)
    monkeypatch.setattr(rm_svc, "send_attachment",
                        lambda sess, att: {"ok": True, "doc_id": "d1",
                                           "name": "x", "folder": "TaskTrack"})
    r = client.post(f"/api/v1/attachments/{att_id}/remarkable")
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True


def test_remarkable_route_passes_through_error_status(client, temp_app, monkeypatch):
    _login(client)
    att_id = _seed_attachment(temp_app)

    def _boom(sess, att):
        raise rm_svc.RemarkableError("unsupported", status_code=415)

    monkeypatch.setattr(rm_svc, "send_attachment", _boom)
    r = client.post(f"/api/v1/attachments/{att_id}/remarkable")
    assert r.status_code == 415
    assert "unsupported" in r.get_json()["error"]
