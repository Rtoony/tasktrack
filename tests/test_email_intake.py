"""Email intake poller — Triage+Assignment unification (Package W4).

The poller no longer auto-commits emails as work_tasks via /api/v1/triage.
It captures each email as a Triage inbox item (POST /api/v1/inbox,
source=email) and uploads attachments to
/api/v1/attachments/inbox_items/<id>. Suggestions are seeded server-side
in the background — the poller never calls /suggest and never creates
tracker rows.

Layers:

1. **Helper-level**: _compose_inbox_fields title/body derivation.
2. **main()-level**: imaplib + requests mocked end to end — payload shape,
   attachment endpoint, \\Seen semantics, and the contract assertion that
   NO call to /api/v1/triage remains.
3. **HTTP-level**: the capture endpoint accepts the triage-scoped token
   the poller already holds (Flask test client, temp DB).
"""
from __future__ import annotations

import sys
from email.message import EmailMessage
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
import email_intake  # noqa: E402

TRIAGE_TOKEN = "test-triage-token"


# ── fixture builders ──────────────────────────────────────────────────────

def _email(subject="Lot 12 grading plan", body="Please update the plan.\nThanks!",
           sender='Dyanna <rtoony@gmail.com>', message_id="<abc123@mail.example>",
           pdf=False):
    msg = EmailMessage()
    if subject:
        msg["Subject"] = subject
    if sender:
        msg["From"] = sender
    if message_id:
        msg["Message-ID"] = message_id
    msg.set_content(body)
    if pdf:
        msg.add_attachment(
            b"%PDF-1.4 fake content",
            maintype="application", subtype="pdf", filename="plan.pdf",
        )
    return msg


def _fake_imap_factory(raw_messages, store_calls):
    """Build an imaplib.IMAP4_SSL stand-in serving `raw_messages` (bytes)."""

    class FakeIMAP:
        def __init__(self, host, port):
            pass

        def login(self, user, password):
            return "OK", []

        def select(self, folder):
            return "OK", [b"1"]

        def search(self, charset, criterion):
            ids = b" ".join(
                str(i + 1).encode() for i in range(len(raw_messages))
            )
            return "OK", [ids]

        def fetch(self, msg_id, spec):
            raw = raw_messages[int(msg_id) - 1]
            return "OK", [(msg_id + b" (RFC822)", raw)]

        def store(self, msg_id, op, flags):
            store_calls.append((msg_id, op, flags))
            return "OK", []

        def close(self):
            return "OK", []

        def logout(self):
            return "OK", []

    return FakeIMAP


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def intake_env(monkeypatch):
    """Hermetic intake env — the developer shell may carry the REAL
    INTAKE_IMAP_* vars (vault injection), so pin every knob the poller
    reads, notably INTAKE_IMAP_SSL=1 so the patched IMAP4_SSL is used."""
    monkeypatch.setenv("INTAKE_IMAP_HOST", "imap.test.local")
    monkeypatch.setenv("INTAKE_IMAP_PORT", "993")
    monkeypatch.setenv("INTAKE_IMAP_USER", "intake@test.local")
    monkeypatch.setenv("INTAKE_IMAP_PASS", "pw")
    monkeypatch.setenv("INTAKE_IMAP_FOLDER", "INBOX")
    monkeypatch.setenv("INTAKE_IMAP_SSL", "1")
    monkeypatch.setenv("INTAKE_MAX_MESSAGES", "10")
    monkeypatch.setenv("INTAKE_MAX_ATTACHMENT_BYTES", str(50 * 1024 * 1024))
    monkeypatch.setenv("TASKTRACK_TOKEN", TRIAGE_TOKEN)
    monkeypatch.setenv("TASKTRACK_URL", "http://tasktrack.test")


def _run_main(monkeypatch, raw_messages, responses):
    """Run email_intake.main() with mocked IMAP + HTTP.

    `responses` is a callable (url, kwargs) -> _FakeResponse.
    Returns (exit_code, posted_calls, store_calls).
    """
    store_calls = []
    posted = []
    fake_cls = _fake_imap_factory(raw_messages, store_calls)
    monkeypatch.setattr(email_intake.imaplib, "IMAP4_SSL", fake_cls)
    monkeypatch.setattr(email_intake.imaplib, "IMAP4", fake_cls)

    def fake_post(url, **kwargs):
        posted.append((url, kwargs))
        return responses(url, kwargs)

    monkeypatch.setattr(email_intake.requests, "post", fake_post)
    code = email_intake.main()
    return code, posted, store_calls


# ── 1. helper-level ───────────────────────────────────────────────────────

def test_compose_inbox_fields_uses_subject_as_title():
    title, body_block, sender = email_intake._compose_inbox_fields(_email())
    assert title == "Lot 12 grading plan"
    assert sender == "Dyanna"
    # Body keeps the composed Subject/From/body block (sender info rides
    # in the body — the capture API has no requested_by field).
    assert body_block.startswith("Subject: Lot 12 grading plan")
    assert "From: Dyanna <rtoony@gmail.com>" in body_block
    assert "Please update the plan." in body_block


def test_title_falls_back_to_first_body_line_truncated():
    long_line = "x" * 400
    msg = _email(subject="", body=f"\n\n{long_line}\nmore text")
    title, body_block, _ = email_intake._compose_inbox_fields(msg)
    assert title == "x" * email_intake._TITLE_MAX
    assert len(title) == 256
    assert "more text" in body_block


def test_title_falls_back_to_sender_for_empty_body():
    msg = _email(subject="", body="")
    title, body_block, _ = email_intake._compose_inbox_fields(msg)
    assert title == "Email from Dyanna"
    assert body_block  # From: line still present -> still captured


# ── 2. main()-level (mocked IMAP + HTTP) ─────────────────────────────────

def _capture_then_attach_responses(url, kwargs):
    if url.endswith("/api/v1/inbox"):
        return _FakeResponse(201, {
            "id": 42, "title": "Lot 12 grading plan", "source": "email",
            "suggested_table": None, "suggestion_json": None,
            "suggested_at": None,
        })
    return _FakeResponse(201, {"id": 7})


def test_main_captures_email_as_inbox_item(monkeypatch, intake_env):
    raw = _email(pdf=True).as_bytes()
    code, posted, stored = _run_main(
        monkeypatch, [raw], _capture_then_attach_responses)
    assert code == 0

    # First POST: inbox capture with the contract payload shape.
    url, kwargs = posted[0]
    assert url == "http://tasktrack.test/api/v1/inbox"
    assert kwargs["headers"]["X-Token"] == TRIAGE_TOKEN
    payload = kwargs["json"]
    assert payload["title"] == "Lot 12 grading plan"
    assert payload["source"] == "email"
    assert payload["priority"] == "Medium"
    assert payload["source_ref"] == "<abc123@mail.example>"
    assert payload["body"].startswith("Subject: Lot 12 grading plan")
    assert "From: Dyanna <rtoony@gmail.com>" in payload["body"]
    # Advisory-only: the poller never asks for a commit or a target.
    assert "commit" not in payload
    assert "target_table" not in payload

    # Second POST: attachment to inbox_items/<returned id>.
    att_url, att_kwargs = posted[1]
    assert att_url == "http://tasktrack.test/api/v1/attachments/inbox_items/42"
    assert att_kwargs["headers"]["X-Token"] == TRIAGE_TOKEN
    assert att_kwargs["files"]["file"][0] == "plan.pdf"

    # Message marked read only after the capture succeeded.
    assert stored == [(b"1", "+FLAGS", "\\Seen")]


def test_main_never_calls_triage_endpoint(monkeypatch, intake_env):
    """Contract: NO call to /api/v1/triage remains anywhere in the flow."""
    raw = _email(pdf=True).as_bytes()
    _, posted, _ = _run_main(
        monkeypatch, [raw], _capture_then_attach_responses)
    assert posted, "expected at least one HTTP call"
    for url, _kwargs in posted:
        assert "/api/v1/triage" not in url
    # Belt and braces: the endpoint string is gone from the module source.
    source = Path(email_intake.__file__).read_text()
    assert "/api/v1/triage" not in source


def test_main_keeps_message_unread_on_capture_failure(monkeypatch, intake_env):
    raw = _email().as_bytes()

    def failing(url, kwargs):
        raise requests.ConnectionError("server down")

    code, posted, stored = _run_main(monkeypatch, [raw], failing)
    # Poller exits 0 (next tick retries) but never marks the message seen.
    assert code == 0
    assert len(posted) == 1
    assert stored == []


def test_main_handles_dedupe_200(monkeypatch, intake_env):
    """A retried message (same Message-ID) gets a 200 with the existing
    row — attachments still target it and the message is marked seen."""
    raw = _email(pdf=True).as_bytes()

    def dedupe(url, kwargs):
        if url.endswith("/api/v1/inbox"):
            return _FakeResponse(200, {
                "id": 42, "suggested_table": "work_tasks",
            })
        return _FakeResponse(201, {"id": 7})

    code, posted, stored = _run_main(monkeypatch, [raw], dedupe)
    assert code == 0
    assert posted[1][0].endswith("/api/v1/attachments/inbox_items/42")
    assert stored == [(b"1", "+FLAGS", "\\Seen")]


# ── 3. HTTP-level: capture accepts the poller's triage-scoped token ──────

@pytest.fixture
def with_triage_token_only(monkeypatch):
    """Server configured with ONLY the triage scope (what the poller holds)."""
    monkeypatch.setenv("TASKTRACK_TOKEN_TRIAGE", TRIAGE_TOKEN)
    monkeypatch.delenv("TASKTRACK_TOKEN_INBOX", raising=False)
    monkeypatch.delenv("TASKTRACK_TOKEN", raising=False)
    import importlib

    from app import tokens
    importlib.reload(tokens)
    yield
    importlib.reload(tokens)


def test_capture_accepts_triage_scope(client, with_triage_token_only):
    r = client.post(
        "/api/v1/inbox",
        json={"title": "Subject line", "body": "From: someone", "source": "email"},
        headers={"X-Token": TRIAGE_TOKEN},
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["source"] == "email"
    # New items carry the (null) suggestion columns from W2's contract.
    assert body["suggested_table"] is None
    assert body["suggestion_json"] is None


def test_capture_still_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setenv("TASKTRACK_TOKEN_INBOX", "the-inbox-token")
    monkeypatch.setenv("TASKTRACK_TOKEN_TRIAGE", TRIAGE_TOKEN)
    monkeypatch.delenv("TASKTRACK_TOKEN", raising=False)
    import importlib

    from app import tokens
    importlib.reload(tokens)
    try:
        r = client.post(
            "/api/v1/inbox",
            json={"title": "x", "source": "email"},
            headers={"X-Token": "not-the-token"},
        )
        assert r.status_code == 401
    finally:
        importlib.reload(tokens)
