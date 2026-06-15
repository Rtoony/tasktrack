"""Triage Phase 2 — deterministic intake-template registry.

Covers:
- Registry plumbing: register() validation (bad route / dup name / bad
  confidence), built-in registration order.
- match / parse / route for each shipped template (cad: prefix,
  [####.##] subject, B&R INTAKE_META form across its sub-targets).
- Suggestion contract: shape matches suggestion_json (target_table,
  category, confidence, fields filtered to ALLOWED_TABLES, model
  "rule:<name>", rationale), no bookkeeping keys leak.
- Precedence: run_suggest_for_item uses a matching template and SKIPS
  the AI classifier; only unmatched input falls through to run_classify.
"""
import json

import pytest

from app.config import ALLOWED_TABLES
from app.db import get_session
from app.models import ActivityLog, InboxItem
from app.services import intake_templates as it

_BOOKKEEPING = ("needs_review", "source", "ai_raw_input", "ai_model")


# ── registry plumbing ───────────────────────────────────────────────────

def test_builtin_registry_order_and_names():
    names = [t.name for t in it.REGISTRY]
    # B&R marker is the strongest signal → first; cad: prefix last.
    assert names == ["br-intake-form", "project-tagged-subject", "cad-prefix"]


def test_register_rejects_invalid_route():
    with pytest.raises(ValueError):
        it.register(it.Template(
            name="bad-route", match=lambda *a: True, parse=lambda *a: {},
            route="inbox_items", rationale="x",
        ))
    with pytest.raises(ValueError):
        it.register(it.Template(
            name="bad-route-2", match=lambda *a: True, parse=lambda *a: {},
            route="nope", rationale="x",
        ))


def test_register_rejects_duplicate_name():
    with pytest.raises(ValueError):
        it.register(it.Template(
            name="cad-prefix", match=lambda *a: True, parse=lambda *a: {},
            route="work_tasks", rationale="dup",
        ))


def test_register_rejects_bad_confidence():
    with pytest.raises(ValueError):
        it.register(it.Template(
            name="bad-conf", match=lambda *a: True, parse=lambda *a: {},
            route="work_tasks", rationale="x", confidence="totally",
        ))


def test_match_skips_template_that_raises():
    boom = it.Template(
        name="explodes", match=lambda *a: (_ for _ in ()).throw(RuntimeError("boom")),
        parse=lambda *a: {}, route="work_tasks", rationale="x",
    )
    it.REGISTRY.insert(0, boom)
    try:
        # A raising match() must not block the cad: template behind it.
        tmpl = it.match_template("cad: still works", "")
        assert tmpl is not None and tmpl.name == "cad-prefix"
    finally:
        it.REGISTRY.remove(boom)


# ── suggestion contract ──────────────────────────────────────────────────

def _assert_contract(s, *, target, model):
    assert s["target_table"] == target
    assert s["model"] == model
    assert s["confidence"] in ("high", "medium", "low")
    assert isinstance(s["fields"], dict)
    allowed = set(ALLOWED_TABLES[target]["fields"])
    for key in s["fields"]:
        assert key in allowed, f"{key} not a {target} field"
        assert key not in _BOOKKEEPING
    if target == "personal_items":
        assert s["category"] in ("Follow-up", "Meetings", "Office", "Assets")
    else:
        assert s["category"] is None
    assert len(s["rationale"]) <= 200


# ── template: cad: prefix → CAD Dev ──────────────────────────────────────

def test_cad_prefix_matches_and_routes():
    s = it.suggest_from_templates("cad: fix the wiggle lisp", "breaks in viewports")
    assert s is not None
    _assert_contract(s, target="work_tasks", model="rule:cad-prefix")
    assert s["fields"]["title"] == "fix the wiggle lisp"
    assert s["fields"]["description"] == "breaks in viewports"


def test_cad_prefix_dash_variant_and_case_insensitive():
    assert it.match_template("CAD - template problem", "").name == "cad-prefix"
    assert it.match_template("Cad: plotting fix", "").name == "cad-prefix"


def test_cad_prefix_does_not_match_mid_word():
    # "cadence" must not trip the cad: prefix.
    assert it.match_template("cadence review next week", "") is None


# ── template: [####.##] subject → Project Task ───────────────────────────

def test_project_subject_matches_and_parses_number():
    s = it.suggest_from_templates("[2301.04] Revise grading exhibit",
                                  "before the agency meeting")
    assert s is not None
    _assert_contract(s, target="project_work_tasks", model="rule:project-tagged-subject")
    assert s["fields"]["project_number"] == "2301.04"
    assert s["fields"]["title"] == "Revise grading exhibit"
    assert s["fields"]["task_description"] == "before the agency meeting"


def test_project_subject_dash_separator():
    s = it.suggest_from_templates("[1588-01] Plan set submittal", "")
    assert s["fields"]["project_number"] == "1588.01"


def test_project_subject_requires_brackets_at_front():
    # A bare project number in the title is NOT this template's shape.
    assert it.match_template("Revise 2301.04 grading", "") is None


# ── template: B&R INTAKE_META form (multi-target) ────────────────────────

def _meta_body(rtype, fields):
    meta = {"type": rtype, "fields": fields}
    return "Request type: x\nINTAKE_META: " + json.dumps(meta)


def test_br_form_cad_route():
    body = _meta_body("cad", {"skill": "LISP", "who": "Pat", "details": "broken"})
    s = it.suggest_from_templates("fix crosshairs", body)
    _assert_contract(s, target="work_tasks", model="rule:br-intake-form")
    assert s["fields"]["cad_skill_area"] == "LISP"
    assert s["fields"]["requested_by"] == "Pat"
    assert s["fields"]["description"] == "broken"


def test_br_form_problem_route_detects_project():
    body = _meta_body("problem", {"details": "basemap deleted from 1234.56",
                                  "involved": "Mark"})
    s = it.suggest_from_templates("issue", body)
    _assert_contract(s, target="personnel_issues", model="rule:br-intake-form")
    assert s["fields"]["issue_description"] == "basemap deleted from 1234.56"
    assert s["fields"]["person_name"] == "Mark"
    assert s["fields"]["project_number"] == "1234.56"


def test_br_form_training_route():
    body = _meta_body("training", {"goals": "associative markups",
                                   "skill": "Bluebeam", "trainees": "Dyanna"})
    s = it.suggest_from_templates("Bluebeam refresher", body)
    _assert_contract(s, target="training_tasks", model="rule:br-intake-form")
    assert s["fields"]["training_goals"] == "associative markups"
    assert s["fields"]["skill_area"] == "Bluebeam"
    assert s["fields"]["trainees"] == "Dyanna"


def test_br_form_general_defaults_to_personal_followup():
    body = _meta_body("general", {"details": "order toner"})
    s = it.suggest_from_templates("order toner", body)
    _assert_contract(s, target="personal_items", model="rule:br-intake-form")
    assert s["category"] == "Follow-up"
    assert s["fields"]["body"] == "order toner"


def test_br_form_general_with_project_number_routes_to_project():
    body = _meta_body("general", {"details": "needed for the 1588.01 submittal"})
    s = it.suggest_from_templates("plan check", body)
    assert s["target_table"] == "project_work_tasks"
    assert s["fields"]["project_number"] == "1588.01"


def test_br_form_malformed_meta_does_not_match():
    # A broken INTAKE_META line yields no match → AI fallback territory.
    assert it.match_template("x", "INTAKE_META: {not json") is None
    assert it.match_template("x", "no meta here") is None


# ── no match falls through ────────────────────────────────────────────────

def test_unmatched_returns_none():
    assert it.suggest_from_templates("just a random note", "no shape here") is None


# ── precedence over the AI classifier (the load-bearing behavior) ────────

def _login(client):
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"


def test_template_takes_precedence_over_run_classify(
        auth_client, temp_app, monkeypatch):
    """A matching template suppresses the AI call entirely."""
    classify_calls = []

    def fake_classify(raw_text, hints=None):
        classify_calls.append(raw_text)
        return ({"target_table": "personal_items", "category": "Follow-up",
                 "confidence": "low", "fields": {"title": "AI guess",
                 "category": "Follow-up", "status": "New"},
                 "model": "ai-model", "rationale": "ai"}, "ai-model")

    monkeypatch.setattr("app.routes.inbox.run_classify", fake_classify)

    r = auth_client.post("/api/v1/inbox",
                         json={"title": "cad: fix the lisp", "body": "vp bug"})
    item_id = r.get_json()["id"]
    r = auth_client.post(f"/api/v1/inbox/{item_id}/suggest")
    assert r.status_code == 200, r.data
    s = r.get_json()["suggestion"]
    assert s["model"] == "rule:cad-prefix"
    assert s["target_table"] == "work_tasks"
    assert classify_calls == []  # the AI classifier was NEVER called

    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, item_id)
        assert item.suggested_table == "work_tasks"
        assert json.loads(item.suggestion_json)["model"] == "rule:cad-prefix"
        log = sess.query(ActivityLog).filter_by(
            table_name="inbox_items", record_id=item_id, action="suggested",
        ).one()
        assert log.new_value == "work_tasks"


def test_unmatched_falls_through_to_run_classify(
        auth_client, temp_app, monkeypatch):
    """No template → the AI classifier still runs (Phase 1 unbroken)."""
    classify_calls = []

    def fake_classify(raw_text, hints=None):
        classify_calls.append(raw_text)
        return ({"target_table": "work_tasks", "category": None,
                 "confidence": "medium", "fields": {"title": "AI titled",
                 "status": "Not Started"}, "model": "ai-model",
                 "rationale": "ai picked it"}, "ai-model")

    monkeypatch.setattr("app.routes.inbox.run_classify", fake_classify)

    r = auth_client.post("/api/v1/inbox",
                         json={"title": "vague note about stuff", "body": "no shape"})
    item_id = r.get_json()["id"]
    r = auth_client.post(f"/api/v1/inbox/{item_id}/suggest")
    assert r.status_code == 200, r.data
    s = r.get_json()["suggestion"]
    assert s["model"] == "ai-model"
    assert len(classify_calls) == 1  # AI fallback fired exactly once


def test_template_suggestion_survives_with_model_down(
        auth_client, temp_app, monkeypatch):
    """Even if run_classify would raise, a template match still succeeds."""
    def boom_classify(raw_text, hints=None):
        raise RuntimeError("classification chain exhausted — local: boom")

    monkeypatch.setattr("app.routes.inbox.run_classify", boom_classify)

    r = auth_client.post("/api/v1/inbox",
                         json={"title": "[2301.04] grading exhibit", "body": ""})
    item_id = r.get_json()["id"]
    r = auth_client.post(f"/api/v1/inbox/{item_id}/suggest")
    assert r.status_code == 200, r.data  # template won; LLM never consulted
    assert r.get_json()["suggestion"]["model"] == "rule:project-tagged-subject"
