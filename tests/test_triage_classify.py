"""Classification mode (Triage+Assignment unification) — Package W1.

Covers:
- run_classify(): JSON parsing/normalization, advisory suggestion shape,
  fields filtered to ALLOWED_TABLES keys, hints steering the user message.
- triage_plan_to_payload(): new personnel_issues / personal_items branches
  (express lane keeps its needs_review=1 behavior).
- suggestion_to_payload(): assignment-time payloads with NO needs_review.
- POST /api/v1/triage accepting the two new express-lane targets with
  unchanged default + needs_review semantics.

The model call is mocked at app.services.triage._triage_call_model (the
LiteLLM gateway) — no network, mirroring how the service isolates the
HTTP layer behind that single function.
"""
import pytest

import app.services.triage as triage_svc
from app.config import ALLOWED_TABLES


# ── helpers ────────────────────────────────────────────────────────────────

def _patch_models(monkeypatch):
    monkeypatch.setattr(triage_svc, "TRIAGE_MODEL_LOCAL", "local-test")
    monkeypatch.setattr(triage_svc, "TRIAGE_MODEL_CLOUD", "cloud-test")


def _fake_gateway(monkeypatch, responses, calls=None):
    """Patch the LiteLLM gateway. `responses` is a dict (every call) or a
    list consumed one per call. `calls` collects call metadata."""
    queue = list(responses) if isinstance(responses, list) else None

    def fake(model, user_message, system_prompt=None):
        if calls is not None:
            calls.append({
                "model": model,
                "user_message": user_message,
                "system_prompt": system_prompt,
            })
        if queue is not None:
            return queue.pop(0)
        return responses

    monkeypatch.setattr(triage_svc, "_triage_call_model", fake)


def _classification(target="work_tasks", **overrides):
    resp = {
        "target_table": target,
        "category": None,
        "confidence": "high",
        "rationale": "Internal CAD tooling work, no project number present.",
        "gist": "Fix the title block LISP routine",
        "checklist": ["Open the routine", "Patch the attribute sync"],
        "fiveMinuteStarter": "Open acaddoc.lsp and find the failing call",
        "missingInfo": ["Which template is affected?"],
        "software": ["autocad"],
        "priority": "High",
        "extras": {},
    }
    resp.update(overrides)
    return resp


_PLAN = {
    "gist": "Drafted gist headline",
    "checklist": ["step one", "step two"],
    "fiveMinuteStarter": "do the first thing",
    "missingInfo": [],
    "software": [],
    "priority": "High",
}


# ── run_classify: parsing + suggestion shape ───────────────────────────────

def test_run_classify_basic_suggestion_shape(monkeypatch):
    _patch_models(monkeypatch)
    _fake_gateway(monkeypatch, _classification(
        extras={"cad_skill_area": "LISP", "requested_by": "Dyanna"},
    ))
    suggestion, model = triage_svc.run_classify("the title block lisp is broken")

    assert model == "local-test"
    assert suggestion["model"] == "local-test"
    assert suggestion["target_table"] == "work_tasks"
    assert suggestion["category"] is None
    assert suggestion["confidence"] == "high"
    assert suggestion["rationale"].startswith("Internal CAD tooling")

    fields = suggestion["fields"]
    assert fields["title"] == "Fix the title block LISP routine"
    assert fields["priority"] == "High"
    assert fields["cad_skill_area"] == "LISP"
    assert fields["requested_by"] == "Dyanna"
    assert "- [ ] Open the routine" in fields["description"]


def test_run_classify_fields_only_valid_target_keys(monkeypatch):
    _patch_models(monkeypatch)
    _fake_gateway(monkeypatch, _classification(
        extras={
            "cad_skill_area": "LISP",
            "hacker_field": "nope",          # not a recognized extra
            "needs_review": 0,               # bookkeeping — must not leak
        },
    ))
    suggestion, _ = triage_svc.run_classify("broken lisp")
    fields = suggestion["fields"]

    allowed = set(ALLOWED_TABLES["work_tasks"]["fields"])
    assert set(fields) <= allowed
    for banned in ("needs_review", "source", "ai_raw_input", "ai_model", "hacker_field"):
        assert banned not in fields


def test_run_classify_normalizes_confidence_and_truncates_rationale(monkeypatch):
    _patch_models(monkeypatch)
    _fake_gateway(monkeypatch, _classification(
        confidence="VERY SURE",  # invalid → "low"
        rationale="r" * 500,
    ))
    suggestion, _ = triage_svc.run_classify("something")
    assert suggestion["confidence"] == "low"
    assert len(suggestion["rationale"]) == 200


def test_run_classify_empty_input_raises():
    with pytest.raises(RuntimeError, match="empty input"):
        triage_svc.run_classify("   ")


# ── run_classify: personnel_issues mapping ─────────────────────────────────

def test_run_classify_personnel_issue_mapping(monkeypatch):
    _patch_models(monkeypatch)
    _fake_gateway(monkeypatch, _classification(
        target="personnel_issues",
        gist="Mark repeatedly exploded xrefs in the plan set",
        checklist=["Review xref workflow with Mark", "Spot-check next plan set"],
        priority="Medium",
        extras={
            "person_name": "Mark Smith",
            "observed_by": "Josh",
            "cad_skill_area": "Xref management",
            "severity": "high",  # case-normalized
        },
    ))
    raw = "Mark exploded the xrefs again on 1234.56, third time this month"
    suggestion, _ = triage_svc.run_classify(raw)

    assert suggestion["target_table"] == "personnel_issues"
    assert suggestion["category"] is None
    fields = suggestion["fields"]
    assert fields["issue_description"] == "Mark repeatedly exploded xrefs in the plan set"
    assert fields["person_name"] == "Mark Smith"
    assert fields["observed_by"] == "Josh"
    assert fields["cad_skill_area"] == "Xref management"
    assert fields["severity"] == "High"
    assert fields["status"] == "Observed"
    assert fields["project_number"] == "1234.56"  # auto-detected from raw text
    assert "- [ ] Review xref workflow with Mark" in fields["recommended_training"]
    assert set(fields) <= set(ALLOWED_TABLES["personnel_issues"]["fields"])

    payload = triage_svc.suggestion_to_payload(suggestion, raw)
    assert payload["issue_description"]  # required field present
    assert "needs_review" not in payload


# ── run_classify: personal_items category handling ─────────────────────────

def test_run_classify_personal_items_category_normalized(monkeypatch):
    _patch_models(monkeypatch)
    _fake_gateway(monkeypatch, _classification(
        target="personal_items",
        category="follow-up",  # lowercase → canonical "Follow-up"
        gist="Chase the toner order with the vendor",
        checklist=["Call vendor", "Confirm delivery date"],
        priority="Low",
    ))
    suggestion, _ = triage_svc.run_classify("did the toner ever ship?")

    assert suggestion["category"] == "Follow-up"
    fields = suggestion["fields"]
    assert fields["category"] == "Follow-up"
    assert fields["title"] == "Chase the toner order with the vendor"
    assert fields["status"] == "New"
    assert set(fields) <= set(ALLOWED_TABLES["personal_items"]["fields"])


def test_run_classify_personal_items_invalid_category_defaults(monkeypatch):
    _patch_models(monkeypatch)
    _fake_gateway(monkeypatch, _classification(
        target="personal_items",
        category="Groceries",  # not in INTERNAL_ITEM_CATEGORIES
        gist="Buy a new chair",
    ))
    suggestion, _ = triage_svc.run_classify("office chair is broken")
    assert suggestion["category"] == "Follow-up"
    assert suggestion["fields"]["category"] == "Follow-up"


# ── run_classify: hints + prompt routing ───────────────────────────────────

def test_run_classify_hints_appear_in_user_message(monkeypatch):
    _patch_models(monkeypatch)
    calls = []
    _fake_gateway(monkeypatch, _classification(), calls=calls)
    triage_svc.run_classify(
        "please update the detail sheet",
        hints={"request_type": "CAD / Drafting", "requested_by": "Pat"},
    )
    msg = calls[0]["user_message"]
    assert "OPERATOR HINTS" in msg
    assert "request_type: CAD / Drafting" in msg
    assert "requested_by: Pat" in msg
    assert msg.startswith("please update the detail sheet")
    # Classification uses its own system prompt, not the express one.
    assert calls[0]["system_prompt"] == triage_svc.TRIAGE_CLASSIFY_SYSTEM_PROMPT


def test_run_classify_no_hints_message_is_raw_text(monkeypatch):
    _patch_models(monkeypatch)
    calls = []
    _fake_gateway(monkeypatch, _classification(), calls=calls)
    triage_svc.run_classify("just the raw note")
    assert calls[0]["user_message"] == "just the raw note"


# ── run_classify: chain behavior ───────────────────────────────────────────

def test_run_classify_falls_through_bad_target_then_succeeds(monkeypatch):
    _patch_models(monkeypatch)
    _fake_gateway(monkeypatch, [
        _classification(target="feedback_items"),  # not a classify target
        _classification(),
    ])
    suggestion, model = triage_svc.run_classify("note")
    assert model == "cloud-test"
    assert suggestion["model"] == "cloud-test"


def test_run_classify_exhausted_raises(monkeypatch):
    _patch_models(monkeypatch)
    _fake_gateway(monkeypatch, [
        {"nonsense": True},
        {"target_table": "work_tasks"},  # valid target but no gist
    ])
    with pytest.raises(RuntimeError, match="classification chain exhausted"):
        triage_svc.run_classify("note")


# ── suggestion_to_payload ──────────────────────────────────────────────────

def test_suggestion_to_payload_personal_items_fallbacks():
    suggestion = {
        "target_table": "personal_items",
        "category": "Office",
        "confidence": "low",
        "fields": {},  # nothing drafted — fall back to raw text
        "model": "m",
        "rationale": "",
    }
    payload = triage_svc.suggestion_to_payload(suggestion, "order toner\nmore detail")
    assert payload["title"] == "order toner"
    assert payload["category"] == "Office"
    assert payload["status"] == "New"
    assert "needs_review" not in payload


def test_suggestion_to_payload_strips_bookkeeping_and_unknown_keys():
    suggestion = {
        "target_table": "work_tasks",
        "category": None,
        "confidence": "high",
        "fields": {
            "title": "Real title",
            "needs_review": 1,        # must never survive
            "source": "email",        # caller stamps source itself
            "ai_model": "x",
            "ai_raw_input": "y",
            "made_up_column": "z",
            "priority": "High",
        },
        "model": "m",
        "rationale": "",
    }
    payload = triage_svc.suggestion_to_payload(suggestion)
    assert payload["title"] == "Real title"
    assert payload["priority"] == "High"
    assert payload["status"] == "Not Started"
    for banned in ("needs_review", "source", "ai_model", "ai_raw_input", "made_up_column"):
        assert banned not in payload


def test_suggestion_to_payload_rejects_unknown_target():
    with pytest.raises(ValueError, match="unsupported target_table"):
        triage_svc.suggestion_to_payload({"target_table": "feedback_items", "fields": {}})
    with pytest.raises(ValueError):
        triage_svc.suggestion_to_payload("not a dict")


# ── express lane: new targets in triage_plan_to_payload ────────────────────

def test_express_targets_extended():
    assert "personnel_issues" in triage_svc.TRIAGE_ALLOWED_TARGETS
    assert "personal_items" in triage_svc.TRIAGE_ALLOWED_TARGETS
    assert triage_svc.TRIAGE_TARGET_LABELS["personnel_issues"] == "Incident Report"
    assert triage_svc.TRIAGE_TARGET_LABELS["personal_items"] == "Internal Item"
    # Default-target behavior depends on work_tasks staying first/present.
    assert triage_svc.TRIAGE_ALLOWED_TARGETS[0] == "work_tasks"


def test_plan_to_payload_personnel_express_keeps_needs_review():
    payload = triage_svc.triage_plan_to_payload(
        dict(_PLAN), "raw note about 1234.56", "test-model", "personnel_issues",
        {"requested_by": "Josh", "cad_skill_area": "Drainage"},
    )
    assert payload["needs_review"] == 1  # express lane semantics unchanged
    assert payload["issue_description"] == "Drafted gist headline"
    assert payload["observed_by"] == "Josh"  # requested_by fallback
    assert payload["cad_skill_area"] == "Drainage"
    assert payload["severity"] == "High"  # mapped from plan priority
    assert payload["status"] == "Observed"
    assert payload["project_number"] == "1234.56"


def test_plan_to_payload_personal_items_express():
    payload = triage_svc.triage_plan_to_payload(
        dict(_PLAN), "raw note", "test-model", "personal_items",
        {"category": "office"},
    )
    assert payload["needs_review"] == 1
    assert payload["title"] == "Drafted gist headline"
    assert payload["category"] == "Office"
    assert payload["status"] == "New"
    assert "- [ ] step one" in payload["body"]


# ── express lane endpoint: POST /api/v1/triage ─────────────────────────────

def _patch_route_run_triage(monkeypatch, plan=None):
    import app.routes.triage as triage_routes

    captured = {}

    def fake_run_triage(raw_text, target="work_tasks", presets=None):
        captured["raw_text"] = raw_text
        captured["target"] = target
        captured["presets"] = presets or {}
        return dict(plan or _PLAN), "fake-model"

    monkeypatch.setattr(triage_routes, "run_triage", fake_run_triage)
    return captured


def test_triage_endpoint_default_target_unchanged(auth_client, monkeypatch):
    captured = _patch_route_run_triage(monkeypatch)
    r = auth_client.post("/api/v1/triage", json={"text": "hello"})
    assert r.status_code == 200
    assert r.get_json()["target_table"] == "work_tasks"
    assert captured["target"] == "work_tasks"


def test_triage_endpoint_commits_personal_item(auth_client, monkeypatch):
    _patch_route_run_triage(monkeypatch)
    r = auth_client.post("/api/v1/triage", json={
        "text": "order toner for the office printer",
        "target_table": "personal_items",
        "category": "Office",
        "commit": True,
    })
    assert r.status_code == 201
    task = r.get_json()["task"]
    assert task["title"] == "Drafted gist headline"
    assert task["category"] == "Office"
    assert task["needs_review"] == 1  # express commits still need review
    assert task["status"] == "New"


def test_triage_endpoint_commits_personnel_issue(auth_client, monkeypatch):
    _patch_route_run_triage(monkeypatch)
    r = auth_client.post("/api/v1/triage", json={
        "text": "Mark broke the xrefs again",
        "target_table": "personnel_issues",
        "person_name": "Mark Smith",
        "observed_by": "Josh",
        "commit": True,
    })
    assert r.status_code == 201
    task = r.get_json()["task"]
    assert task["issue_description"] == "Drafted gist headline"
    assert task["person_name"] == "Mark Smith"
    assert task["observed_by"] == "Josh"
    assert task["status"] == "Observed"


def test_triage_endpoint_rejects_non_triage_target(auth_client, monkeypatch):
    _patch_route_run_triage(monkeypatch)
    r = auth_client.post("/api/v1/triage", json={
        "text": "x", "target_table": "feedback_items",
    })
    assert r.status_code == 400
