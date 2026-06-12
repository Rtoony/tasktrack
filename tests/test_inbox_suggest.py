"""Triage+Assignment unification — Package W2 (schema + inbox API).

Covers:
- Alembic migration b9e4a7c3d2f8: applies on a fresh temp DB, satisfies
  the app's fail-loud schema check, and downgrades cleanly.
- POST /api/v1/inbox/<id>/suggest: stores + returns the ADVISORY
  suggestion (classifier mocked), idempotent overwrite, 502 on model
  failure with the item untouched, auth (session / inbox token / triage
  token), INTAKE_META hint extraction.
- Capture auto-suggest: the synchronous helpers are tested directly
  (auto_suggest_enabled gate + _auto_suggest_worker body) — never the
  thread itself, which is skipped under pytest by design.
- /api/v1/intake/submit: deterministic rule seed per request type
  (model 'rule:request-type', confidence 'high').
- Promote ("Assignment"): structured 400 {"error", "missing"} on absent
  required fields, needs_review never written, personnel_issues works
  end-to-end, activity log notes AI-vs-human target disagreement.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from app.db import get_session
from app.models import ActivityLog, InboxItem, PersonnelIssue, WorkTask

ROOT = Path(__file__).resolve().parent.parent

INBOX_TOKEN = "test-inbox-token"
TRIAGE_TOKEN = "test-triage-token"


# ── helpers / fixtures ─────────────────────────────────────────────────────

def _reload_tokens():
    import importlib

    from app import tokens
    importlib.reload(tokens)


@pytest.fixture
def with_inbox_token(monkeypatch):
    monkeypatch.setenv("TASKTRACK_TOKEN_INBOX", INBOX_TOKEN)
    _reload_tokens()
    yield
    _reload_tokens()


@pytest.fixture
def with_triage_token(monkeypatch):
    monkeypatch.setenv("TASKTRACK_TOKEN_TRIAGE", TRIAGE_TOKEN)
    _reload_tokens()
    yield
    _reload_tokens()


def _fake_suggestion(target="work_tasks", **overrides):
    s = {
        "target_table": target,
        "category": None,
        "confidence": "high",
        "fields": {"title": "Drafted title", "priority": "High",
                   "status": "Not Started"},
        "model": "fake-model",
        "rationale": "looks like internal CAD tooling work",
    }
    s.update(overrides)
    return s


def _mock_classifier(monkeypatch, suggestion=None, calls=None, error=None):
    def fake(raw_text, hints=None):
        if calls is not None:
            calls.append({"raw_text": raw_text, "hints": hints})
        if error is not None:
            raise error
        s = suggestion or _fake_suggestion()
        return s, s["model"]

    monkeypatch.setattr("app.routes.inbox.run_classify", fake)


def _capture_item(auth_client, title="fix the lisp", body="details here"):
    r = auth_client.post("/api/v1/inbox", json={"title": title, "body": body})
    assert r.status_code == 201, r.data
    return r.get_json()["id"]


# ── migration ──────────────────────────────────────────────────────────────

def _alembic_cfg():
    from alembic.config import Config as AlembicConfig
    return AlembicConfig(str(ROOT / "alembic.ini"))


def _inbox_columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(inbox_items)")}
    finally:
        conn.close()


def test_migration_upgrades_and_matches_models(tmp_path, monkeypatch):
    from alembic import command

    db_path = tmp_path / "migrated.db"
    monkeypatch.setenv("TASKTRACK_DATABASE_URL", f"sqlite:///{db_path}")
    command.upgrade(_alembic_cfg(), "head")

    cols = _inbox_columns(db_path)
    assert {"suggested_table", "suggestion_json", "suggested_at"} <= cols

    # The app's fail-loud boot check must accept a freshly migrated DB.
    from app import _check_schema_matches_models
    _check_schema_matches_models(str(db_path))  # raises on drift


def test_migration_downgrade_removes_columns(tmp_path, monkeypatch):
    from alembic import command

    db_path = tmp_path / "migrated.db"
    monkeypatch.setenv("TASKTRACK_DATABASE_URL", f"sqlite:///{db_path}")
    cfg = _alembic_cfg()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    cols = _inbox_columns(db_path)
    assert not {"suggested_table", "suggestion_json", "suggested_at"} & cols
    command.upgrade(cfg, "head")  # round-trips back up
    assert {"suggested_table", "suggestion_json", "suggested_at"} <= _inbox_columns(db_path)


# ── POST /api/v1/inbox/<id>/suggest ────────────────────────────────────────

def test_suggest_stores_and_returns(auth_client, temp_app, monkeypatch):
    item_id = _capture_item(auth_client)
    _mock_classifier(monkeypatch)

    r = auth_client.post(f"/api/v1/inbox/{item_id}/suggest")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["suggestion"]["target_table"] == "work_tasks"
    assert body["suggestion"]["model"] == "fake-model"
    assert body["inbox_item"]["suggested_table"] == "work_tasks"
    assert body["inbox_item"]["suggested_at"] is not None

    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.suggested_table == "work_tasks"
        stored = json.loads(item.suggestion_json)
        assert stored == body["suggestion"]
        assert item.suggested_at is not None
        log = sess.query(ActivityLog).filter_by(
            table_name="inbox_items", record_id=item_id, action="suggested",
        ).all()
        assert len(log) == 1
        assert log[0].new_value == "work_tasks"


def test_suggest_rerun_overwrites(auth_client, temp_app, monkeypatch):
    item_id = _capture_item(auth_client)
    _mock_classifier(monkeypatch)
    assert auth_client.post(f"/api/v1/inbox/{item_id}/suggest").status_code == 200

    _mock_classifier(monkeypatch, suggestion=_fake_suggestion(
        target="personal_items", category="Office",
        fields={"title": "Drafted title", "category": "Office", "status": "New"},
    ))
    r = auth_client.post(f"/api/v1/inbox/{item_id}/suggest")
    assert r.status_code == 200
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.suggested_table == "personal_items"
        assert json.loads(item.suggestion_json)["category"] == "Office"


def test_suggest_model_failure_returns_502_item_untouched(
        auth_client, temp_app, monkeypatch):
    item_id = _capture_item(auth_client)
    _mock_classifier(monkeypatch, error=RuntimeError(
        "classification chain exhausted — local: boom"))

    r = auth_client.post(f"/api/v1/inbox/{item_id}/suggest")
    assert r.status_code == 502
    body = r.get_json()
    assert body["error"] == "suggest failed"
    assert "classification chain exhausted" in body["detail"]
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.suggested_table is None
        assert item.suggestion_json is None
        assert item.suggested_at is None


def test_suggest_404_on_missing_item(auth_client, monkeypatch):
    _mock_classifier(monkeypatch)
    assert auth_client.post("/api/v1/inbox/99999/suggest").status_code == 404


def test_suggest_rejects_unauthenticated(client, monkeypatch):
    _mock_classifier(monkeypatch)
    r = client.post("/api/v1/inbox/1/suggest")
    assert r.status_code == 401


def test_suggest_accepts_inbox_token(client, with_inbox_token, monkeypatch):
    created = client.post(
        "/api/v1/inbox",
        json={"title": "token capture", "source": "bot"},
        headers={"X-Token": INBOX_TOKEN},
    )
    item_id = created.get_json()["id"]
    _mock_classifier(monkeypatch)
    r = client.post(f"/api/v1/inbox/{item_id}/suggest",
                    headers={"X-Token": INBOX_TOKEN})
    assert r.status_code == 200, r.data


def test_suggest_accepts_triage_token(
        client, with_inbox_token, with_triage_token, monkeypatch):
    created = client.post(
        "/api/v1/inbox",
        json={"title": "email capture", "source": "email"},
        headers={"X-Token": INBOX_TOKEN},
    )
    item_id = created.get_json()["id"]
    _mock_classifier(monkeypatch)
    r = client.post(f"/api/v1/inbox/{item_id}/suggest",
                    headers={"X-Token": TRIAGE_TOKEN})
    assert r.status_code == 200, r.data


def test_suggest_forwards_intake_meta_hints(auth_client, monkeypatch):
    meta = {"type": "cad", "suggested_target": "work_tasks",
            "fields": {"skill": "LISP / Automation", "software": "AutoCAD",
                       "project": "", "details": "long text"}}
    body = "Request type: CAD / Drafting\ndetails: x\nINTAKE_META: " + json.dumps(meta)
    item_id = _capture_item(auth_client, title="fix crosshairs", body=body)

    calls = []
    _mock_classifier(monkeypatch, calls=calls)
    assert auth_client.post(f"/api/v1/inbox/{item_id}/suggest").status_code == 200
    hints = calls[0]["hints"]
    assert hints["request_type"] == "cad"
    assert hints["requested_target"] == "work_tasks"
    assert hints["skill"] == "LISP / Automation"
    assert hints["software"] == "AutoCAD"
    assert "project" not in hints  # empty values dropped
    assert calls[0]["raw_text"].startswith("fix crosshairs\n\n")


def test_intake_hints_defensive_on_malformed_meta():
    from app.routes.inbox import _intake_hints
    assert _intake_hints("INTAKE_META: {not json") is None
    assert _intake_hints("INTAKE_META: [1,2]") is None
    assert _intake_hints("no meta line at all") is None
    assert _intake_hints("") is None
    assert _intake_hints(None) is None


# ── capture auto-suggest (synchronous helpers, not the thread) ─────────────

def test_auto_suggest_enabled_default_on(temp_app):
    from app.routes.inbox import auto_suggest_enabled
    assert temp_app.config["INBOX_AUTO_SUGGEST"] is True
    assert auto_suggest_enabled(temp_app) is True


def test_auto_suggest_respects_config_flag(temp_app):
    from app.routes.inbox import auto_suggest_enabled
    temp_app.config["INBOX_AUTO_SUGGEST"] = False
    assert auto_suggest_enabled(temp_app) is False


def test_auto_suggest_skips_when_models_unconfigured(temp_app, monkeypatch):
    import app.services.triage as triage_svc
    from app.routes.inbox import auto_suggest_enabled
    monkeypatch.setattr(triage_svc, "TRIAGE_MODEL_LOCAL", "")
    monkeypatch.setattr(triage_svc, "TRIAGE_MODEL_CLOUD", "")
    assert auto_suggest_enabled(temp_app) is False


def test_auto_suggest_worker_stores_suggestion(auth_client, temp_app, monkeypatch):
    from app.routes.inbox import _auto_suggest_worker
    item_id = _capture_item(auth_client, title="background note", body="")
    _mock_classifier(monkeypatch)

    _auto_suggest_worker(temp_app, item_id)  # synchronous call of thread body

    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.suggested_table == "work_tasks"
        assert json.loads(item.suggestion_json)["model"] == "fake-model"
        # Background path logs the activity row as System.
        log = sess.query(ActivityLog).filter_by(
            table_name="inbox_items", record_id=item_id, action="suggested",
        ).one()
        assert log.user_name == "System"


def test_auto_suggest_worker_swallows_model_failure(
        auth_client, temp_app, monkeypatch):
    from app.routes.inbox import _auto_suggest_worker
    item_id = _capture_item(auth_client, title="will fail", body="")
    _mock_classifier(monkeypatch, error=RuntimeError("chain exhausted"))

    _auto_suggest_worker(temp_app, item_id)  # must not raise

    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.suggested_table is None


def test_capture_response_still_fast_shape(auth_client):
    """Capture returns the item immediately — suggestion fields are
    present (None) but never populated synchronously."""
    r = auth_client.post("/api/v1/inbox", json={"title": "quick capture"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["suggested_table"] is None
    assert body["suggestion_json"] is None
    assert body["suggested_at"] is None


# ── intake submit: deterministic rule seed per request type ────────────────

def _submit(auth_client, rtype, fields, **extra):
    payload = {"type": rtype, "fields": fields}
    payload.update(extra)
    return auth_client.post("/api/v1/intake/submit", json=payload)


def _stored_suggestion(temp_app, inbox_id):
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, inbox_id)
        assert item.suggestion_json, "suggestion_json not seeded"
        return item.suggested_table, json.loads(item.suggestion_json), item


def test_intake_seed_cad(auth_client, temp_app):
    r = _submit(auth_client, "cad", {
        "summary": "Fix the wiggle lisp",
        "details": "Breaks inside viewports",
        "skill": "LISP / Automation",
        "who": "Pat",
    }, desired_by="2026-07-01")
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["suggested_table"] == "work_tasks"

    table, suggestion, item = _stored_suggestion(temp_app, body["inbox_id"])
    assert table == "work_tasks"
    assert suggestion["model"] == "rule:request-type"
    assert suggestion["confidence"] == "high"
    assert suggestion["category"] is None
    assert item.suggested_at is not None
    fields = suggestion["fields"]
    assert fields["title"] == "Fix the wiggle lisp"
    assert fields["description"] == "Breaks inside viewports"
    assert fields["cad_skill_area"] == "LISP / Automation"
    assert fields["requested_by"] == "Pat"
    assert fields["due_date"] == "2026-07-01"
    for banned in ("needs_review", "source", "ai_raw_input", "ai_model"):
        assert banned not in fields


def test_intake_seed_project_work_detects_number(auth_client, temp_app):
    r = _submit(auth_client, "project_work", {
        "summary": "Revise grading exhibit",
        "project": "2301.04 — Water Plant",
        "details": "Update before the agency meeting",
    })
    assert r.status_code == 201
    table, suggestion, _item = _stored_suggestion(temp_app, r.get_json()["inbox_id"])
    assert table == "project_work_tasks"
    fields = suggestion["fields"]
    assert fields["project_number"] == "2301.04"
    assert fields["project_name"] == "2301.04 — Water Plant"
    assert fields["task_description"] == "Update before the agency meeting"


def test_intake_seed_training(auth_client, temp_app):
    r = _submit(auth_client, "training", {
        "topic": "Bluebeam markups",
        "goals": "Get comfortable with associative markups",
        "trainees": "Dyanna",
        "skill": "Bluebeam",
    })
    assert r.status_code == 201
    table, suggestion, _item = _stored_suggestion(temp_app, r.get_json()["inbox_id"])
    assert table == "training_tasks"
    fields = suggestion["fields"]
    assert fields["title"] == "Bluebeam markups"
    assert fields["training_goals"] == "Get comfortable with associative markups"
    assert fields["trainees"] == "Dyanna"
    assert fields["skill_area"] == "Bluebeam"


def test_intake_seed_problem_routes_to_personnel_issues(auth_client, temp_app):
    r = _submit(auth_client, "problem", {
        "details": "Survey basemap deleted from project 1234.56",
        "involved": "Mark Smith",
    }, severity="High")
    assert r.status_code == 201
    table, suggestion, _item = _stored_suggestion(temp_app, r.get_json()["inbox_id"])
    assert table == "personnel_issues"
    fields = suggestion["fields"]
    assert fields["issue_description"] == "Survey basemap deleted from project 1234.56"
    assert fields["severity"] == "High"
    assert fields["person_name"] == "Mark Smith"
    assert fields["project_number"] == "1234.56"


def test_intake_seed_general_defaults_to_personal_followup(auth_client, temp_app):
    r = _submit(auth_client, "general", {
        "summary": "Order toner",
        "details": "Front office printer is low",
    })
    assert r.status_code == 201
    table, suggestion, _item = _stored_suggestion(temp_app, r.get_json()["inbox_id"])
    assert table == "personal_items"
    assert suggestion["category"] == "Follow-up"
    fields = suggestion["fields"]
    assert fields["category"] == "Follow-up"
    assert fields["title"] == "Order toner"
    assert fields["body"] == "Front office printer is low"


def test_intake_seed_general_with_project_number_routes_to_project(
        auth_client, temp_app):
    r = _submit(auth_client, "general", {
        "summary": "Check the plan set",
        "details": "Needed for the 1588.01 submittal next week",
    })
    assert r.status_code == 201
    table, suggestion, _item = _stored_suggestion(temp_app, r.get_json()["inbox_id"])
    assert table == "project_work_tasks"
    assert suggestion["fields"]["project_number"] == "1588.01"


def test_intake_seed_suggestion_type_is_personal(auth_client, temp_app):
    r = _submit(auth_client, "suggestion", {
        "title": "Standing desk for plot room",
        "body": "Would help during long plot sessions",
    })
    assert r.status_code == 201
    table, suggestion, _item = _stored_suggestion(temp_app, r.get_json()["inbox_id"])
    assert table == "personal_items"
    assert suggestion["category"] == "Follow-up"
    assert suggestion["fields"]["body"] == "Would help during long plot sessions"


def test_intake_seed_logs_suggested_activity(auth_client, temp_app):
    r = _submit(auth_client, "cad", {"summary": "log check"})
    inbox_id = r.get_json()["inbox_id"]
    with temp_app.app_context():
        sess = get_session()
        log = sess.query(ActivityLog).filter_by(
            table_name="inbox_items", record_id=inbox_id, action="suggested",
        ).one()
        assert log.new_value == "work_tasks"


# ── promote ("Assignment") ─────────────────────────────────────────────────

def test_promote_400_lists_missing_required_fields(auth_client):
    item_id = _capture_item(auth_client, title="bare item", body="")
    r = auth_client.post(f"/api/v1/inbox/{item_id}/promote",
                         json={"target_table": "project_work_tasks"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "missing required fields"
    assert set(body["missing"]) == {"project_name", "project_number",
                                    "task_description"}


def test_promote_400_personal_items_missing_category(auth_client):
    item_id = _capture_item(auth_client, title="needs a category", body="")
    r = auth_client.post(f"/api/v1/inbox/{item_id}/promote",
                         json={"target_table": "personal_items"})
    assert r.status_code == 400
    assert r.get_json()["missing"] == ["category"]


def test_promote_never_writes_needs_review(auth_client, temp_app):
    item_id = _capture_item(auth_client, title="reviewed by hand", body="desc")
    r = auth_client.post(f"/api/v1/inbox/{item_id}/promote", json={
        "target_table": "work_tasks",
        "overrides": {"needs_review": 1, "description": "human-reviewed"},
    })
    assert r.status_code == 201, r.data
    new_id = r.get_json()["promoted_to"]["id"]
    with temp_app.app_context():
        sess = get_session()
        wt = sess.get(WorkTask, new_id)
        assert wt.needs_review == 0
        assert wt.description == "human-reviewed"


def test_promote_personnel_issues_end_to_end(auth_client, temp_app):
    item_id = _capture_item(
        auth_client,
        title="Mark broke the xrefs",
        body="Exploded xrefs on the plan set again",
    )
    r = auth_client.post(f"/api/v1/inbox/{item_id}/promote", json={
        "target_table": "personnel_issues",
        "overrides": {"person_name": "Mark Smith", "observed_by": "Josh"},
    })
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["promoted_to"]["table"] == "personnel_issues"
    with temp_app.app_context():
        sess = get_session()
        issue = sess.get(PersonnelIssue, body["promoted_to"]["id"])
        assert issue.issue_description == "Exploded xrefs on the plan set again"
        assert issue.person_name == "Mark Smith"
        assert issue.observed_by == "Josh"
        assert issue.severity == "Medium"  # mapped from item priority
        assert issue.status == "Observed"
        item = sess.get(InboxItem, item_id)
        assert item.status == "Archived"
        assert item.promoted_to_table == "personnel_issues"


def test_promote_personnel_issues_falls_back_to_title(auth_client, temp_app):
    item_id = _capture_item(auth_client, title="Plotter ate the mylar", body="")
    r = auth_client.post(f"/api/v1/inbox/{item_id}/promote",
                         json={"target_table": "personnel_issues"})
    assert r.status_code == 201, r.data
    with temp_app.app_context():
        sess = get_session()
        issue = sess.get(PersonnelIssue, r.get_json()["promoted_to"]["id"])
        assert issue.issue_description == "Plotter ate the mylar"


def test_promote_logs_ai_disagreement(auth_client, temp_app):
    item_id = _capture_item(auth_client, title="disagreement", body="desc")
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        item.suggested_table = "personal_items"
        item.suggestion_json = json.dumps(_fake_suggestion(target="personal_items"))
        sess.commit()

    r = auth_client.post(f"/api/v1/inbox/{item_id}/promote",
                         json={"target_table": "work_tasks"})
    assert r.status_code == 201, r.data
    new_id = r.get_json()["promoted_to"]["id"]
    with temp_app.app_context():
        sess = get_session()
        log = sess.query(ActivityLog).filter_by(
            table_name="inbox_items", record_id=item_id, action="promoted",
        ).one()
        assert log.new_value == (
            f"assigned to work_tasks#{new_id} (AI suggested personal_items)"
        )
        # Suggestion columns survive promote as history.
        item = sess.get(InboxItem, item_id)
        assert item.suggested_table == "personal_items"
        assert item.suggestion_json


def test_promote_matching_suggestion_logs_plain(auth_client, temp_app):
    item_id = _capture_item(auth_client, title="agreement", body="desc")
    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        item.suggested_table = "work_tasks"
        sess.commit()

    r = auth_client.post(f"/api/v1/inbox/{item_id}/promote",
                         json={"target_table": "work_tasks"})
    assert r.status_code == 201
    new_id = r.get_json()["promoted_to"]["id"]
    with temp_app.app_context():
        sess = get_session()
        log = sess.query(ActivityLog).filter_by(
            table_name="inbox_items", record_id=item_id, action="promoted",
        ).one()
        assert log.new_value == f"work_tasks#{new_id}"
