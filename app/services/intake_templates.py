"""Deterministic intake-template registry — Triage Phase 2.

The mirror image of the external Submission Forms: a versioned, tested
registry of *known input shapes* that parse deterministically into a
tracker suggestion BEFORE (and instead of) the AI classifier.

Each template declares the four parts from TRIAGE_DESIGN.md Phase 2:

    name   — stable identifier, surfaces as model="rule:<name>".
    match  — does this raw item look like this shape? (source + pattern)
    parse  — deterministic field extraction (project #, requested_by, …).
    route  — target_table (+ category for personal_items).
    trust  — auto_file / min_confidence / required-fields gate (Phase 2b).

When an inbox item matches a template the suggestion is built locally and
the LLM call is skipped, raising confidence for known shapes and keeping
the row useful when the model is down. Unmatched input falls through to
``run_classify`` (the AI fallback). Templates always take precedence.

The emitted suggestion matches the ``suggestion_json`` contract exactly
(see app/services/triage.py / app/routes/inbox.py):

    {"target_table", "category" (personal_items only, else None),
     "confidence" ("high"|"medium"|"low"), "fields" (only keys valid for
     the target per ALLOWED_TABLES, no needs_review/source/ai_* keys),
     "model" ("rule:<template>"), "rationale" (<=200 chars)}

Storage is a Python registry module by design (versioned + tested);
TRIAGE_DESIGN graduates it to a managed table + admin UI only if Josh
edits templates often. ADVISORY ONLY — like every suggestion, nothing
here auto-creates a tracker row; auto-file is the separate Phase-2b gate.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import ALLOWED_TABLES, INTERNAL_ITEM_CATEGORIES

# Bookkeeping keys never emitted in a suggestion's fields dict — matches
# the classifier + rule-seed contracts elsewhere in the codebase.
_SUGGESTION_FIELD_EXCLUDES = ("needs_review", "source", "ai_raw_input", "ai_model")

# Confidence ordering (low < medium < high) for the Phase-2b min_confidence
# floor comparison. Anything unrecognized sorts as the lowest rank so a bad
# value can never accidentally clear a floor.
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _confidence_rank(value) -> int:
    return _CONFIDENCE_RANK.get(str(value or "").strip().lower(), -1)

# A ####.## civil project number, e.g. 1234.56 (also tolerant of 1234-56).
_PROJECT_RE = re.compile(r"(?<!\d)(\d{4})[.\-](\d{2})(?!\d)")

# request_type → tracker, shared with the B&R intake route's seed map so
# the registry and the route agree on routing for explicitly-typed forms.
_RTYPE_TARGET_MAP = {
    "cad": "work_tasks",
    "project_work": "project_work_tasks",
    "training": "training_tasks",
    "problem": "personnel_issues",
}


def _detect_project_number(*texts: str) -> str:
    for text in texts:
        m = _PROJECT_RE.search(str(text or ""))
        if m:
            return f"{m.group(1)}.{m.group(2)}"
    return ""


def _normalize_category(value, default: str = "Follow-up") -> str:
    raw = str(value or "").strip()
    for cat in INTERNAL_ITEM_CATEGORIES:
        if cat.lower() == raw.lower():
            return cat
    return default


def _filter_fields(target: str, seed: dict) -> dict:
    """Keep only keys valid for the target table, dropping bookkeeping."""
    allowed = set(ALLOWED_TABLES[target]["fields"]) - set(_SUGGESTION_FIELD_EXCLUDES)
    return {
        k: v for k, v in seed.items()
        if k in allowed and v is not None and str(v).strip() != ""
    }


@dataclass(frozen=True)
class Trust:
    """Phase-2b gating metadata. Inert until INBOX_AUTO_FILE ships.

    auto_file        — may this template ever auto-file (Phase 2b)?
    min_confidence   — confidence floor required before auto-file.
    requires_complete— every required field of the target must be present.
    """
    auto_file: bool = False
    min_confidence: str = "high"
    requires_complete: bool = True


@dataclass(frozen=True)
class Template:
    """One deterministic input shape.

    match(title, body, source) -> bool
    parse(title, body, source) -> dict  (raw extracted values; route+filter
                                          happen in build_suggestion)
    """
    name: str
    match: Callable[[str, str, str], bool]
    parse: Callable[[str, str, str], dict]
    route: str                       # target_table
    rationale: str                   # one-line why, <=200 chars
    category: str | None = None      # personal_items only
    confidence: str = "high"
    trust: Trust = field(default_factory=Trust)


# ── registry plumbing ────────────────────────────────────────────────────

REGISTRY: list[Template] = []


def register(template: Template) -> Template:
    """Add a template to the ordered registry (first match wins).

    Raises on a duplicate name or an invalid route so a bad template
    fails loud at import time, not silently at runtime.
    """
    if template.route not in ALLOWED_TABLES or template.route == "inbox_items":
        raise ValueError(f"template {template.name!r}: invalid route {template.route!r}")
    if any(t.name == template.name for t in REGISTRY):
        raise ValueError(f"duplicate template name: {template.name!r}")
    if template.confidence not in ("high", "medium", "low"):
        raise ValueError(f"template {template.name!r}: bad confidence {template.confidence!r}")
    REGISTRY.append(template)
    return template


def match_template(title: str, body: str = "", source: str = "") -> Template | None:
    """Return the first registered template that matches, else None.

    Defensive: a template whose match() raises is skipped (a buggy
    template must never break the suggestion path) — the next template,
    and ultimately the AI fallback, still get their chance.
    """
    title = title or ""
    body = body or ""
    source = source or ""
    for tmpl in REGISTRY:
        try:
            if tmpl.match(title, body, source):
                return tmpl
        except Exception:  # noqa: BLE001 — a broken template never blocks triage
            continue
    return None


def build_suggestion(tmpl: Template, title: str, body: str = "",
                     source: str = "") -> dict:
    """Render a matched template into a suggestion_json-shaped dict."""
    raw = tmpl.parse(title or "", body or "", source or "") or {}
    fields = _filter_fields(tmpl.route, raw)

    category = None
    if tmpl.route == "personal_items":
        category = _normalize_category(raw.get("category") or tmpl.category)
        fields["category"] = category

    return {
        "target_table": tmpl.route,
        "category": category,
        "confidence": tmpl.confidence,
        "fields": fields,
        "model": f"rule:{tmpl.name}",
        "rationale": (tmpl.rationale or "")[:200],
    }


# ── INTAKE_META parsing (shared with the B&R intake route shape) ──────────

def _parse_intake_meta(body: str) -> dict | None:
    """Pull the JSON from a body's ``INTAKE_META: {...}`` line, defensively.

    Returns the parsed dict, or None when no well-formed meta line exists.
    """
    for line in (body or "").splitlines():
        line = line.strip()
        if not line.startswith("INTAKE_META:"):
            continue
        try:
            meta = json.loads(line[len("INTAKE_META:"):].strip())
        except (ValueError, TypeError):
            return None
        return meta if isinstance(meta, dict) else None
    return None


# ── Template #1: B&R submission form (INTAKE_META marker) ─────────────────
#
# Mirrors the external Submission Form. The form embeds an INTAKE_META JSON
# line carrying the request type + raw fields; route deterministically off
# the request type, exactly like the intake route's capture-time seed — but
# now ANY inbox item carrying that marker (e.g. one captured via the inbox
# API rather than the web form) gets the same deterministic suggestion
# instead of an LLM round-trip.

def _br_match(title: str, body: str, source: str) -> bool:
    return _parse_intake_meta(body) is not None


def _br_parse(title: str, body: str, source: str) -> dict:
    meta = _parse_intake_meta(body) or {}
    fields = meta.get("fields") if isinstance(meta.get("fields"), dict) else {}
    rtype = str(meta.get("type") or "").strip()

    details = str(fields.get("details") or "").strip()
    long_text = details or str(fields.get("body") or "").strip()
    detected = _detect_project_number(
        fields.get("project_number"), fields.get("project"), title, long_text, body,
    )

    target = _RTYPE_TARGET_MAP.get(rtype)
    if target is None:
        target = "project_work_tasks" if detected else "personal_items"

    if target == "work_tasks":
        seed = {"title": title, "description": long_text, "status": "Not Started"}
        if fields.get("skill"):
            seed["cad_skill_area"] = str(fields["skill"]).strip()
        if fields.get("who"):
            seed["requested_by"] = str(fields["who"]).strip()
    elif target == "project_work_tasks":
        seed = {
            "title": title,
            "project_name": str(fields.get("project") or "").strip() or title,
            "project_number": detected,
            "task_description": long_text or title,
            "status": "Not Started",
        }
    elif target == "training_tasks":
        seed = {
            "title": title,
            "training_goals": str(fields.get("goals") or "").strip() or long_text,
            "status": "Not Started",
        }
        if fields.get("skill"):
            seed["skill_area"] = str(fields["skill"]).strip()
        trainees = str(fields.get("trainees") or fields.get("who") or "").strip()
        if trainees:
            seed["trainees"] = trainees
    elif target == "personnel_issues":
        seed = {"issue_description": long_text or title, "status": "Observed"}
        if fields.get("involved"):
            seed["person_name"] = str(fields["involved"]).strip()
        if detected:
            seed["project_number"] = detected
    else:  # personal_items
        seed = {
            "title": title,
            "category": _normalize_category(fields.get("category")),
            "body": long_text,
            "status": "New",
        }
    # Stash the resolved target so build_suggestion routes correctly: the
    # B&R shape is multi-target, so it can't use a single static route.
    seed["__target__"] = target
    return seed


# The B&R form is multi-target, so build_suggestion (single static route)
# can't render it. suggest_from_templates dispatches the B&R match here.
def _br_suggestion(title: str, body: str, source: str) -> dict | None:
    raw = _br_parse(title, body, source)
    target = raw.pop("__target__")
    fields = _filter_fields(target, raw)
    category = None
    if target == "personal_items":
        category = _normalize_category(raw.get("category"))
        fields["category"] = category
    label = ALLOWED_TABLES[target]["label"]
    rtype = str((_parse_intake_meta(body) or {}).get("type") or "").strip()
    if rtype in _RTYPE_TARGET_MAP:
        rationale = f"B&R intake form request type '{rtype}' routes to {label}."
    else:
        rationale = f"B&R intake form routes to {label}."
    return {
        "target_table": target,
        "category": category,
        "confidence": "high",
        "fields": fields,
        "model": "rule:br-intake-form",
        "rationale": rationale[:200],
    }


# ── Template #2: [####.##]-tagged subject → Project Task ──────────────────
#
# The email-subject / quick-capture shape: a bracketed project number at
# the front of the title, e.g. "[2301.04] Revise grading exhibit". Common
# convention for forwarded project emails.

_BRACKET_PROJECT_RE = re.compile(r"^\s*\[\s*(\d{4})[.\-](\d{2})\s*\]\s*(.*)$")


def _project_subject_match(title: str, body: str, source: str) -> bool:
    return bool(_BRACKET_PROJECT_RE.match(title or ""))


def _project_subject_parse(title: str, body: str, source: str) -> dict:
    m = _BRACKET_PROJECT_RE.match(title or "")
    proj = f"{m.group(1)}.{m.group(2)}"
    rest = (m.group(3) or "").strip() or title.strip()
    return {
        "title": rest[:256],
        "project_number": proj,
        "project_name": rest[:120] or proj,
        "task_description": (body or "").strip() or rest,
        "status": "Not Started",
    }


# ── Template #3: "cad:" prefix → CAD Dev Task ─────────────────────────────
#
# The Telegram / quick-capture shape: a "cad:" (or "cad -") prefix on the
# title marks internal CAD-development work, e.g.
# "cad: fix the wiggle lisp in viewports".

_CAD_PREFIX_RE = re.compile(r"^\s*cad\s*[:\-]\s*(.+)$", re.IGNORECASE)


def _cad_prefix_match(title: str, body: str, source: str) -> bool:
    return bool(_CAD_PREFIX_RE.match(title or ""))


def _cad_prefix_parse(title: str, body: str, source: str) -> dict:
    m = _CAD_PREFIX_RE.match(title or "")
    rest = (m.group(1) or "").strip()
    return {
        "title": rest[:256] or "CAD task",
        "description": (body or "").strip() or rest,
        "status": "Not Started",
    }


# ── Built-in registration (order = match precedence) ──────────────────────
#
# The B&R form marker is the strongest, most-structured signal, so it's
# first. Then the bracketed-project subject, then the cad: prefix.

register(Template(
    name="br-intake-form",
    match=_br_match,
    # parse is unused for B&R (it's multi-target — see _br_suggestion) but
    # the Template contract requires a callable; route is a placeholder.
    parse=_br_parse,
    route="personal_items",  # nominal; real routing happens in _br_suggestion
    rationale="B&R intake form (INTAKE_META) — deterministic route.",
    trust=Trust(auto_file=False, min_confidence="high", requires_complete=True),
))

register(Template(
    name="project-tagged-subject",
    match=_project_subject_match,
    parse=_project_subject_parse,
    route="project_work_tasks",
    rationale="Subject tagged with a [####.##] project number → Project Task.",
    trust=Trust(auto_file=False, min_confidence="high", requires_complete=True),
))

register(Template(
    name="cad-prefix",
    match=_cad_prefix_match,
    parse=_cad_prefix_parse,
    route="work_tasks",
    rationale="'cad:' prefix marks internal CAD-development work → CAD Dev.",
    trust=Trust(auto_file=False, min_confidence="high", requires_complete=True),
))


def match_and_build(title: str, body: str = "",
                    source: str = "") -> tuple[Template, dict] | None:
    """Match a template and build its suggestion, returning BOTH.

    Returns ``(template, suggestion)`` or None when nothing matched.
    Single shared core for both ``suggest_from_templates`` (which only
    needs the suggestion) and the Phase-2b auto-file gate (which also
    needs the matched template's ``Trust`` metadata). Handles the
    multi-target B&R form via its dedicated builder.
    """
    tmpl = match_template(title, body, source)
    if tmpl is None:
        return None
    if tmpl.name == "br-intake-form":
        built = _br_suggestion(title or "", body or "", source or "")
        if built is None:
            # Defensive: the multi-target build failed — don't claim a match.
            return None
        return tmpl, built
    return tmpl, build_suggestion(tmpl, title, body, source)


# The B&R template is multi-target, so suggest_from_templates routes it to
# its dedicated builder rather than the generic single-route one.
def suggest_from_templates(title: str, body: str = "",
                           source: str = "") -> dict | None:
    """Match + build a deterministic suggestion, or None when no template fits.

    Templates take precedence over the AI classifier; this is the single
    entry point the inbox suggest flow calls before falling back to
    ``run_classify``.
    """
    result = match_and_build(title, body, source)
    return result[1] if result is not None else None


# ── Phase 2b: confidence-gated auto-file eligibility ──────────────────────
#
# A suggestion auto-files ONLY when ALL hold (TRIAGE_DESIGN Phase 2b):
#   1. It came from a deterministic template whose Trust.auto_file is True.
#   2. Its confidence meets the template's Trust.min_confidence floor.
#   3. (When Trust.requires_complete) every REQUIRED field of the resolved
#      target table is present and non-empty in the drafted fields.
# The global INBOX_AUTO_FILE kill-switch is enforced by the CALLER, not
# here — this helper is a pure, side-effect-free predicate so it stays
# testable and the kill-switch decision lives in one obvious place.

def auto_file_decision(title: str, body: str = "",
                       source: str = "") -> dict | None:
    """Evaluate Phase-2b auto-file eligibility for raw inbox text.

    Returns None when no template matched at all (so the caller knows to
    leave the AI classifier path untouched). Otherwise returns a dict:

        {"template": Template, "suggestion": dict, "eligible": bool,
         "reason": str, "missing": list[str]}

    ``eligible`` is True only when the template is trusted, the confidence
    clears the floor, and required fields are complete. ``reason`` /
    ``missing`` explain a refusal for the audit trail. PURE — never writes.
    """
    result = match_and_build(title, body, source)
    if result is None:
        return None
    tmpl, suggestion = result
    trust = tmpl.trust
    target = suggestion.get("target_table")
    fields = suggestion.get("fields") or {}

    if not trust.auto_file:
        return {"template": tmpl, "suggestion": suggestion, "eligible": False,
                "reason": "template not trusted for auto-file", "missing": []}

    if _confidence_rank(suggestion.get("confidence")) < _confidence_rank(trust.min_confidence):
        return {"template": tmpl, "suggestion": suggestion, "eligible": False,
                "reason": (f"confidence {suggestion.get('confidence')!r} below "
                           f"floor {trust.min_confidence!r}"),
                "missing": []}

    missing: list[str] = []
    if trust.requires_complete:
        if target not in ALLOWED_TABLES or target == "inbox_items":
            return {"template": tmpl, "suggestion": suggestion, "eligible": False,
                    "reason": f"invalid target {target!r}", "missing": []}
        required = ALLOWED_TABLES[target]["required"]
        missing = [req for req in required
                   if not str(fields.get(req) or "").strip()]
        if missing:
            return {"template": tmpl, "suggestion": suggestion, "eligible": False,
                    "reason": "required fields incomplete", "missing": missing}

    return {"template": tmpl, "suggestion": suggestion, "eligible": True,
            "reason": "trusted template, confidence met, fields complete",
            "missing": []}


__all__ = [
    "Template",
    "Trust",
    "REGISTRY",
    "register",
    "match_template",
    "build_suggestion",
    "match_and_build",
    "suggest_from_templates",
    "auto_file_decision",
]
