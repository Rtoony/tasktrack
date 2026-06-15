"""Triage Phase 2b — confidence-gated auto-file.

Auto-file files an inbox item straight into its target tracker (bypassing
manual triage) ONLY when ALL hold:
  1. INBOX_AUTO_FILE kill-switch is ON (default OFF).
  2. The match came from a deterministic template with Trust.auto_file=True.
  3. The suggestion's confidence meets the template's min_confidence floor.
  4. Every required field of the target is present in the drafted payload.

These tests prove:
- The kill-switch defaults OFF, and OFF is a strict no-op vs Phase 2
  (item stays in the inbox, no tracker row created).
- The pure auto_file_decision() predicate gates correctly on each rule
  (untrusted, confidence floor, incomplete required fields, eligible).
- When ON + a trusted template clears every gate, the item is filed via
  the SAME assignment path (needs_review=0, archived, audit-logged).
- The built-in templates ship with auto_file=False, so even ON is inert
  for them — graduation is a deliberate Phase-3-driven act.
"""
import json

import pytest

from app.db import get_session
from app.models import ActivityLog, InboxItem, WorkTask
from app.services import intake_templates as it


# ── helpers / fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def trusted_cad_template(monkeypatch):
    """Register a TRUSTED clone of the cad: template for the duration of a
    test, restoring the original registry afterward. Avoids mutating the
    shipped (untrusted) built-ins."""
    saved = list(it.REGISTRY)
    trusted = it.Template(
        name="test-trusted-cad",
        match=lambda title, body, source: (title or "").lower().startswith("autofile:"),
        parse=lambda title, body, source: {
            "title": (title or "")[len("autofile:"):].strip() or "Auto task",
            "description": (body or "").strip() or "auto",
            "status": "Not Started",
        },
        route="work_tasks",
        rationale="test trusted template",
        confidence="high",
        trust=it.Trust(auto_file=True, min_confidence="high", requires_complete=True),
    )
    # Prepend so it wins precedence over the built-ins.
    monkeypatch.setattr(it, "REGISTRY", [trusted, *saved])
    yield trusted


def _capture(auth_client, title, body=""):
    r = auth_client.post("/api/v1/inbox", json={"title": title, "body": body})
    assert r.status_code == 201, r.data
    return r.get_json()["id"]


def _run_suggest(temp_app, item_id):
    """Drive the synchronous suggest/auto-file core directly (no thread)."""
    from app.routes.inbox import run_suggest_for_item
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        suggestion = run_suggest_for_item(sess, item)
        sess.commit()
        return suggestion


# ── kill-switch default + config wiring ────────────────────────────────────

def test_auto_file_default_off(temp_app):
    from app.routes.inbox import auto_file_enabled
    assert temp_app.config["INBOX_AUTO_FILE"] is False
    assert auto_file_enabled(temp_app) is False


def test_auto_file_profile_default_is_off():
    """The flag itself defaults OFF at the profile layer (no env set)."""
    import importlib

    import app.profile as profile
    importlib.reload(profile)
    try:
        assert profile.INBOX_AUTO_FILE is False
        assert profile.summary()["INBOX_AUTO_FILE"] is False
    finally:
        importlib.reload(profile)


def test_auto_file_env_on(monkeypatch):
    import importlib

    import app.profile as profile
    monkeypatch.setenv("INBOX_AUTO_FILE", "true")
    importlib.reload(profile)
    try:
        assert profile.INBOX_AUTO_FILE is True
    finally:
        monkeypatch.delenv("INBOX_AUTO_FILE", raising=False)
        importlib.reload(profile)


# ── pure decision predicate ────────────────────────────────────────────────

def test_decision_none_when_no_template():
    assert it.auto_file_decision("just a random note", "", "") is None


def test_decision_untrusted_builtin_not_eligible():
    """The shipped cad: template matches but is auto_file=False → ineligible."""
    d = it.auto_file_decision("cad: fix the wiggle lisp", "", "")
    assert d is not None
    assert d["eligible"] is False
    assert "not trusted" in d["reason"]
    assert d["template"].name == "cad-prefix"


def test_decision_eligible_for_trusted_complete(trusted_cad_template):
    d = it.auto_file_decision("autofile: do the thing", "with detail", "")
    assert d is not None
    assert d["eligible"] is True
    assert d["suggestion"]["target_table"] == "work_tasks"
    assert d["template"].name == "test-trusted-cad"


def test_decision_confidence_floor_blocks(monkeypatch):
    """A trusted template whose suggestion confidence is below the floor is
    refused with a confidence reason."""
    saved = list(it.REGISTRY)
    tmpl = it.Template(
        name="test-lowconf",
        match=lambda t, b, s: (t or "").lower().startswith("lowconf:"),
        parse=lambda t, b, s: {"title": "x", "description": "y", "status": "Not Started"},
        route="work_tasks",
        rationale="low conf",
        confidence="medium",  # below the high floor
        trust=it.Trust(auto_file=True, min_confidence="high", requires_complete=True),
    )
    monkeypatch.setattr(it, "REGISTRY", [tmpl, *saved])
    d = it.auto_file_decision("lowconf: thing", "", "")
    assert d["eligible"] is False
    assert "below" in d["reason"]


def test_decision_incomplete_required_blocks(monkeypatch):
    """A trusted template that fails to supply a required field is refused,
    listing the missing field(s) for the audit trail."""
    saved = list(it.REGISTRY)
    tmpl = it.Template(
        name="test-incomplete",
        match=lambda t, b, s: (t or "").lower().startswith("incomplete:"),
        # project_work_tasks requires project_name/number/task_description/title;
        # supply only title so the gate trips.
        parse=lambda t, b, s: {"title": "only a title"},
        route="project_work_tasks",
        rationale="incomplete",
        confidence="high",
        trust=it.Trust(auto_file=True, min_confidence="high", requires_complete=True),
    )
    monkeypatch.setattr(it, "REGISTRY", [tmpl, *saved])
    d = it.auto_file_decision("incomplete: x", "", "")
    assert d["eligible"] is False
    assert d["reason"] == "required fields incomplete"
    assert set(d["missing"]) >= {"project_name", "project_number", "task_description"}


# ── OFF is a strict no-op (default behavior) ───────────────────────────────

def test_off_is_no_op_even_with_trusted_template(
        auth_client, temp_app, trusted_cad_template):
    """Switch OFF (default): a trusted, fully-eligible item still just gets a
    suggestion — it is NOT filed, stays New, creates no tracker row."""
    assert temp_app.config["INBOX_AUTO_FILE"] is False
    item_id = _capture(auth_client, "autofile: build the widget", "all the detail")
    _run_suggest(temp_app, item_id)

    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.status == "New"
        assert (item.promoted_to_table or "") == ""
        assert item.promoted_to_id is None
        # advisory suggestion present, but no auto_filed activity row
        assert item.suggested_table == "work_tasks"
        assert sess.query(WorkTask).count() == 0
        assert sess.query(ActivityLog).filter_by(
            table_name="inbox_items", record_id=item_id, action="auto_filed",
        ).count() == 0


def test_off_untrusted_builtin_no_op(auth_client, temp_app):
    """Even with the switch flipped ON, a shipped (untrusted) template never
    auto-files — graduation is deliberate."""
    temp_app.config["INBOX_AUTO_FILE"] = True
    item_id = _capture(auth_client, "cad: fix the crosshair lisp", "details")
    _run_suggest(temp_app, item_id)
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.status == "New"
        assert sess.query(WorkTask).count() == 0


# ── ON + trusted template → auto-files via the assignment path ─────────────

def test_on_trusted_auto_files(auth_client, temp_app, trusted_cad_template):
    temp_app.config["INBOX_AUTO_FILE"] = True
    item_id = _capture(auth_client, "autofile: wire the panel", "panel detail")
    _run_suggest(temp_app, item_id)

    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        # inbox item archived + linked
        assert item.status == "Archived"
        assert item.promoted_to_table == "work_tasks"
        assert item.promoted_to_id is not None
        # tracker row created via the assignment path — NO needs_review
        wt = sess.get(WorkTask, item.promoted_to_id)
        assert wt is not None
        assert wt.needs_review == 0
        # Title is the inbox item's own title — the assignment path treats it
        # as canonical (same as a human promote), not the template's parse.
        assert wt.title == "autofile: wire the panel"
        assert wt.source == "auto-file:test-trusted-cad"
        # audit trail
        log = sess.query(ActivityLog).filter_by(
            table_name="inbox_items", record_id=item_id, action="auto_filed",
        ).one()
        assert "auto-file:test-trusted-cad" in log.new_value
        assert f"work_tasks#{item.promoted_to_id}" in log.new_value


def test_on_trusted_auto_file_idempotent(auth_client, temp_app, trusted_cad_template):
    """A second suggest run on an already-auto-filed (archived) item does not
    file a duplicate."""
    temp_app.config["INBOX_AUTO_FILE"] = True
    item_id = _capture(auth_client, "autofile: once only", "x")
    _run_suggest(temp_app, item_id)
    with temp_app.app_context():
        sess = get_session()
        first_count = sess.query(WorkTask).count()
    # Re-run: maybe_auto_file must bail because the item is Archived/promoted.
    from app.routes.inbox import maybe_auto_file
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert maybe_auto_file(sess, item) is None
        sess.commit()
        assert sess.query(WorkTask).count() == first_count


def test_capture_endpoint_off_does_not_auto_file(auth_client, temp_app,
                                                  trusted_cad_template):
    """End-to-end through the real capture endpoint with the switch OFF: the
    item lands in the inbox, never in a tracker (auto-suggest thread is
    skipped under pytest, so this asserts the default capture contract)."""
    assert temp_app.config["INBOX_AUTO_FILE"] is False
    r = auth_client.post("/api/v1/inbox",
                         json={"title": "autofile: via endpoint", "body": "d"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["status"] == "New"
    assert (body.get("promoted_to_table") or "") == ""
