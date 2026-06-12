"""AI Intake (triage) — converts messy input into a structured ActionPlan.

Two modes share the same LiteLLM gateway and JSON plumbing:

- Express lane (`run_triage`): caller pre-picks the target table, model
  drafts the plan, commit path sets needs_review=1 (unchanged behavior).
- Classification mode (`run_classify`): the model picks the target table
  itself and returns an ADVISORY suggestion (Triage+Assignment
  unification). Suggestions never auto-create tracker rows; rows created
  from them at assignment time carry NO needs_review flag.

Calls LiteLLM (`LITELLM_BASE_URL`) with a local-first chain
(`TRIAGE_MODEL_LOCAL` then `TRIAGE_MODEL_CLOUD`). Pure helpers — route
handlers in app/routes/triage.py wire these to HTTP, write to the DB,
and apply the AI data policy decided in Phase 1B.

Phase 1B will add: cloud-fallback opt-in flag, per-call audit row,
raw-input retention setting. Phase 6 adds the purge cron and full
retention machinery. Phase 7 (or later) decouples model choice from
env-vars to admin config.
"""
import json
import os
import re
import time

import requests

from ..config import ALLOWED_TABLES, INTERNAL_ITEM_CATEGORIES

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = (
    os.environ.get("LITELLM_API_KEY")
    or os.environ.get("LITELLM_MASTER_KEY")
    or ""
)
TRIAGE_MODEL_LOCAL = os.environ.get("TRIAGE_MODEL_LOCAL", "qwen3-coder")
TRIAGE_MODEL_CLOUD = os.environ.get("TRIAGE_MODEL_CLOUD", "gemini-flash")
TRIAGE_TIMEOUT_S = int(os.environ.get("TRIAGE_TIMEOUT_S", "90"))

TRIAGE_SYSTEM_PROMPT = """You are the TaskTrack Intake agent — a civil-engineering-aware
project manager who turns messy notes, emails, and quick captures into a clear,
actionable task draft. Your tone is direct, momentum-building, and practical:
"here is what this actually means, here is the first honest step." Never pad,
never moralize, never add generic productivity advice.

You will receive raw input that may be a forwarded email, a voice transcript, a
pasted wall of text, or a quick note. The operator may append an "OPERATOR
PRESETS" block after the raw input. Treat those presets as hard constraints —
do not override them, do not debate them, just obey them (locked priority
stays locked, stated skill area stays stated, target-table framing shapes
your tone).

Return a JSON object (and nothing else) with this exact schema:

{
  "gist": string,                // one-sentence distilled headline, <=120 chars
  "checklist": string[],         // concrete, ordered action steps
  "fiveMinuteStarter": string,   // the smallest next physical step, <=180 chars
  "missingInfo": string[],       // questions that must be resolved before execution
  "software": string[],          // CAD / engineering tools likely involved (AutoCAD, Civil 3D, Bluebeam, etc.), lowercase short tags, may be empty
  "priority": string             // "Low" | "Medium" | "High"
}

Rules:
- If the input is empty or nonsensical, still return the schema with best-effort
  placeholders and list the ambiguity under missingInfo.
- Prefer civil-engineering terminology when the input hints at it (grading,
  drainage, sheets, details, revisions, redlines, markup, submittal, etc.).
- If the target is the Training tracker, frame the checklist as learning
  steps (watch/read/practice/demonstrate), not construction work.
- If the target is the Project Work tracker, emphasize deliverables, sheets
  touched, and timing over process chatter.
- If the target is the Incident Report tracker, the gist must be a factual,
  blame-free one-line summary of the incident or capability gap; frame the
  checklist as follow-up / coaching / verification steps, not construction work.
- If the target is the Internal Item tracker, treat the input as the
  operator's own follow-up: keep the gist short and personal, and frame the
  checklist as simple personal next steps (ask, schedule, buy, file, reply).
- Output JSON only. No prose, no markdown fences, no preamble.
"""

TRIAGE_ALLOWED_TARGETS = (
    "work_tasks",
    "project_work_tasks",
    "training_tasks",
    "personnel_issues",
    "personal_items",
)
TRIAGE_TARGET_LABELS = {
    "work_tasks": "CAD Development",
    "project_work_tasks": "Project Work",
    "training_tasks": "Training",
    "personnel_issues": "Incident Report",
    "personal_items": "Internal Item",
}

# Classification mode targets — the full unified-triage list. Kept as its
# own name so the express lane and the classifier can diverge later
# without breaking importers (W2/W3 import this for suggestion handling).
TRIAGE_CLASSIFY_TARGETS = TRIAGE_ALLOWED_TARGETS

TRIAGE_PRESET_KEYS = (
    "priority",
    "category",
    "person_name",
    "observed_by",
    "severity",
    "cad_skill_area",
    "skill_area",
    "requested_by",
    "request_reference",
    "due_date",
    "due_at",
    "scheduled_completion_at",
    "time_required_minutes",
    "notes",
    "scope_notes",
    "progress_notes",
    "confirmation_notes",
    "completion_notes",
    "project_number",
    "project_name",
    "billing_phase",
    "engineer",
    "trainees",
    "source",
)

TRIAGE_CONFIRM_TABLES = {"work_tasks", "project_work_tasks", "training_tasks", "personal_items"}


def _triage_build_user_message(raw_text, presets, target):
    body = (raw_text or "").strip()
    hints = []
    label = TRIAGE_TARGET_LABELS.get(target, "CAD Development")
    hints.append(f"Target tracker: {label}")
    locked_priority = (presets.get("priority") or "").strip().title()
    if locked_priority in ("Low", "Medium", "High"):
        hints.append(f"Priority is LOCKED to {locked_priority} — return exactly this value.")
    locked_skill = (presets.get("cad_skill_area") or presets.get("skill_area") or "").strip()
    if locked_skill:
        key = "CAD skill area" if target != "training_tasks" else "Training skill area"
        hints.append(f"{key} is: {locked_skill}")
    if (presets.get("requested_by") or "").strip():
        hints.append(f"Requested by: {presets['requested_by'].strip()}")
    if (presets.get("source") or "").strip():
        hints.append(f"Captured from: {presets['source'].strip()}")
    if target == "project_work_tasks":
        if (presets.get("project_number") or "").strip():
            hints.append(f"Project number: {presets['project_number'].strip()}")
        if (presets.get("project_name") or "").strip():
            hints.append(f"Project name: {presets['project_name'].strip()}")
    if target == "personnel_issues":
        if (presets.get("person_name") or "").strip():
            hints.append(f"People involved: {presets['person_name'].strip()}")
        if (presets.get("observed_by") or "").strip():
            hints.append(f"Observed by: {presets['observed_by'].strip()}")
        locked_severity = (presets.get("severity") or "").strip().title()
        if locked_severity in ("Low", "Medium", "High", "Critical"):
            hints.append(f"Severity is LOCKED to {locked_severity}.")
    if target == "personal_items":
        if (presets.get("category") or "").strip():
            hints.append(f"Internal category: {presets['category'].strip()}")
    if not hints:
        return body
    parts = [body, "", "---", "OPERATOR PRESETS (honor these exactly):"]
    parts.extend(f"- {h}" for h in hints)
    return "\n".join(parts)


def _triage_extract_json(text):
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _triage_call_model(model, raw_text, system_prompt=None):
    headers = {"Content-Type": "application/json"}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt or TRIAGE_SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    # One retry on transport-level failures (gateway blip, cold model)
    # before giving up on this model — email-sourced inputs are lost if
    # the whole chain errors out.
    last_exc = None
    for attempt in (1, 2):
        try:
            resp = requests.post(
                f"{LITELLM_BASE_URL.rstrip('/')}/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=TRIAGE_TIMEOUT_S,
            )
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt == 1:
                time.sleep(2)
    else:
        raise last_exc
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return _triage_extract_json(content)


def _triage_normalize_plan(plan):
    if not isinstance(plan, dict):
        return None

    def _as_str(v):
        return str(v).strip() if v is not None else ""

    def _as_str_list(v):
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in re.split(r"\r?\n|•|(?<!\d),\s*", v)]
            return [p for p in parts if p]
        if isinstance(v, list):
            return [_as_str(item) for item in v if _as_str(item)]
        return []

    priority = _as_str(plan.get("priority")).title() or "Medium"
    if priority not in ("Low", "Medium", "High"):
        priority = "Medium"

    return {
        "gist": _as_str(plan.get("gist"))[:500],
        "checklist": _as_str_list(plan.get("checklist")),
        "fiveMinuteStarter": _as_str(plan.get("fiveMinuteStarter") or plan.get("starter") or "")[:500],
        "missingInfo": _as_str_list(plan.get("missingInfo") or plan.get("clarifications")),
        "software": _as_str_list(plan.get("software") or plan.get("tools")),
        "priority": priority,
    }


def run_triage(raw_text, target="work_tasks", presets=None):
    """Run the triage chain. Returns (plan_dict, model_used) or raises RuntimeError."""
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise RuntimeError("empty input")
    presets = presets or {}
    user_message = _triage_build_user_message(raw_text, presets, target)

    errors = []
    for model in (TRIAGE_MODEL_LOCAL, TRIAGE_MODEL_CLOUD):
        if not model:
            continue
        try:
            plan = _triage_call_model(model, user_message)
        except Exception as exc:  # noqa: BLE001 — record and try the next model
            errors.append(f"{model}: {exc}")
            continue
        normalized = _triage_normalize_plan(plan)
        if normalized and normalized["gist"]:
            # Enforce priority lock server-side even if the model ignored it.
            locked = (presets.get("priority") or "").strip().title()
            if locked in ("Low", "Medium", "High"):
                normalized["priority"] = locked
            return normalized, model
        errors.append(f"{model}: unparseable response")

    raise RuntimeError("triage chain exhausted — " + " | ".join(errors))


def _triage_auto_project_number(text):
    m = re.search(r"\b(\d{4}\.\d{2})\b", text or "")
    return m.group(1) if m else ""


def _triage_preset_str(presets, *keys):
    for k in keys:
        val = presets.get(k)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            return s
    return ""


def _normalize_internal_category(value, default="Follow-up"):
    """Case-insensitive match against INTERNAL_ITEM_CATEGORIES with fallback."""
    raw = str(value or "").strip()
    for cat in INTERNAL_ITEM_CATEGORIES:
        if cat.lower() == raw.lower():
            return cat
    return default


def _normalize_severity(value, default="Medium"):
    sev = str(value or "").strip().title()
    return sev if sev in ("Low", "Medium", "High", "Critical") else default


def _triage_context_block(plan):
    lines = []
    starter = plan.get("fiveMinuteStarter") or ""
    if starter:
        lines.append(f"**Start here →** {starter}")
    clarifications = plan.get("missingInfo") or []
    if clarifications:
        lines.append("**Questions to resolve:**")
        lines.extend(f"- {q}" for q in clarifications)
    software = plan.get("software") or []
    if software:
        lines.append("**Software:** " + ", ".join(software))
    return "\n".join(lines)


def triage_plan_to_payload(plan, raw_text, model, target, presets):
    """Render an AI plan into a row payload ready for create_direct_record."""
    checklist_md = "\n".join(f"- [ ] {item}" for item in plan.get("checklist") or []) or ""
    gist = plan.get("gist") or (raw_text.splitlines()[0][:120] if raw_text else "Untitled intake")
    priority = plan.get("priority") or "Medium"
    source = _triage_preset_str(presets, "source") or "paste"

    common_ai = {
        "needs_review": 1,
        "source": source,
        "ai_raw_input": raw_text[:8000],
        "ai_model": model,
    }

    if target == "work_tasks":
        payload = {
            "title": gist,
            "description": checklist_md,
            "priority": priority,
            "status": "Not Started",
            "starter_note": plan.get("fiveMinuteStarter") or "",
            "clarifications_needed": json.dumps(plan.get("missingInfo") or []),
            "software": json.dumps(plan.get("software") or []),
        }
        for key in ("cad_skill_area", "requested_by", "request_reference", "due_date", "notes"):
            val = _triage_preset_str(presets, key)
            if val:
                payload[key] = val

    elif target == "project_work_tasks":
        proj_num = _triage_preset_str(presets, "project_number") or _triage_auto_project_number(raw_text)
        proj_name = _triage_preset_str(presets, "project_name") or gist[:80]
        context_block = _triage_context_block(plan)
        task_desc = checklist_md + (("\n\n" + context_block) if context_block else "")
        payload = {
            "title": gist,
            "project_name": proj_name,
            "project_number": proj_num,
            "task_description": task_desc,
            "priority": priority,
            "status": "Not Started",
        }
        for key in (
            "billing_phase", "engineer", "due_at",
            "scheduled_completion_at", "time_required_minutes",
            "notes", "scope_notes", "progress_notes",
            "confirmation_notes", "completion_notes",
        ):
            val = _triage_preset_str(presets, key)
            if val:
                payload[key] = val
        if not payload.get("engineer"):
            fallback_engineer = _triage_preset_str(presets, "requested_by")
            if fallback_engineer:
                payload["engineer"] = fallback_engineer

    elif target == "training_tasks":
        context_block = _triage_context_block(plan)
        payload = {
            "title": gist,
            "training_goals": checklist_md,
            "additional_context": context_block,
            "priority": priority,
            "status": "Not Started",
        }
        skill = _triage_preset_str(presets, "skill_area", "cad_skill_area")
        if skill:
            payload["skill_area"] = skill
        for key in ("trainees", "requested_by", "due_date", "notes"):
            val = _triage_preset_str(presets, key)
            if val:
                payload[key] = val
        if not payload.get("trainees"):
            fallback_trainees = _triage_preset_str(presets, "requested_by")
            if fallback_trainees:
                payload["trainees"] = fallback_trainees

    elif target == "personnel_issues":
        context_block = _triage_context_block(plan)
        payload = {
            "issue_description": gist,
            "severity": _normalize_severity(
                _triage_preset_str(presets, "severity") or priority
            ),
            "status": "Observed",
        }
        recommended = _triage_preset_str(presets, "recommended_training") or checklist_md
        if recommended:
            payload["recommended_training"] = recommended
        incident_context = _triage_preset_str(presets, "incident_context") or context_block
        if incident_context:
            payload["incident_context"] = incident_context
        for key in ("person_name", "observed_by", "cad_skill_area", "project_number"):
            val = _triage_preset_str(presets, key)
            if val:
                payload[key] = val
        if not payload.get("observed_by"):
            fallback_observer = _triage_preset_str(presets, "requested_by")
            if fallback_observer:
                payload["observed_by"] = fallback_observer
        if not payload.get("project_number"):
            detected = _triage_auto_project_number(raw_text)
            if detected:
                payload["project_number"] = detected

    elif target == "personal_items":
        context_block = _triage_context_block(plan)
        body = checklist_md + (("\n\n" + context_block) if context_block else "")
        payload = {
            "title": gist,
            "category": _normalize_internal_category(
                _triage_preset_str(presets, "category")
            ),
            "body": body,
            "priority": priority,
            "status": "New",
        }
        for key in ("due_date", "source_ref"):
            val = _triage_preset_str(presets, key)
            if val:
                payload[key] = val

    else:
        raise ValueError(f"unsupported target_table: {target}")

    payload.update(common_ai)
    return payload


# ── Classification mode (Triage+Assignment unification) ────────────────────
#
# The inbox is the dump ground; the SYSTEM suggests a category + drafted
# fields; the HUMAN has final say at promote ("Assignment") time.
# `run_classify` picks the target table itself (vs. the express lane where
# the caller pre-picks) and returns an ADVISORY suggestion dict. Suggestions
# never auto-create tracker rows, and rows created from them at assignment
# time carry NO needs_review flag — a human just reviewed them.

TRIAGE_CLASSIFY_SYSTEM_PROMPT = """You are the TaskTrack Assignment classifier — a
civil-engineering-aware project manager who reads one messy captured note
(email, voice transcript, pasted text, quick capture) and decides which
tracker it belongs in, then drafts the task fields for that tracker. Your
tone is direct and practical. Never pad, never moralize.

First, pick exactly ONE "target_table" from this list:

- "work_tasks" (CAD Dev): internal CAD development & support work —
  AutoCAD / Civil 3D / LISP routines, CAD tooling, standards, templates,
  blocks, plotting/setup fixes. Not tied to one numbered client project.
- "project_work_tasks" (Project Task): deliverable work on a numbered
  client project — sheets, submittals, redlines, grading/drainage plans,
  agency responses. Project numbers look like 1234.56.
- "training_tasks" (Training): staff learning — coaching, courses,
  practice, demonstrations, skill-building for one or more staff members.
- "personnel_issues" (Incident Report): personnel/capability observations
  or incidents — a skill gap, repeated mistake, process or equipment
  incident, lost time, coaching need tied to how someone works.
- "personal_items" (Internal Item): the operator's own follow-ups,
  meeting prep, office/admin chores, or asset/equipment notes. When you
  pick this target you MUST also set "category" to exactly one of:
  "Follow-up", "Meetings", "Office", "Assets". For every other target,
  "category" must be null.

The user message may end with an "OPERATOR HINTS" block. Treat those hints
as strong steers — they usually decide the target and key fields.

Then draft the task with the same discipline as the intake agent:

- "gist": one-sentence distilled headline, <=120 chars
- "checklist": concrete, ordered action steps (learning steps for
  Training; follow-up/coaching steps for Incident Report; simple personal
  next steps for Internal Item)
- "fiveMinuteStarter": the smallest next physical step, <=180 chars
- "missingInfo": questions that must be resolved before execution
- "software": CAD / engineering tools likely involved, lowercase short
  tags, may be empty
- "priority": "Low" | "Medium" | "High"

Return a JSON object (and nothing else) with this exact schema:

{
  "target_table": string,        // one of the five tables above
  "category": string|null,       // personal_items only, else null
  "confidence": string,          // "high" | "medium" | "low"
  "rationale": string,           // one line, <=200 chars, why this target
  "gist": string,
  "checklist": string[],
  "fiveMinuteStarter": string,
  "missingInfo": string[],
  "software": string[],
  "priority": string,
  "extras": object               // optional, per-target keys below; omit unknowns
}

Allowed "extras" keys by target (only include values you can actually
infer from the input — never invent):
- work_tasks: cad_skill_area, requested_by, request_reference, due_date
- project_work_tasks: project_number, project_name, engineer, billing_phase
- training_tasks: skill_area, trainees, requested_by, due_date
- personnel_issues: person_name, observed_by, cad_skill_area,
  severity ("Low"|"Medium"|"High"|"Critical"), recommended_training,
  project_number
- personal_items: due_date

Confidence guide: "high" = unambiguous fit; "medium" = plausible but
another target could also fit; "low" = best guess from thin input.

Rules:
- Prefer civil-engineering terminology when the input hints at it.
- If the input is empty or nonsensical, still return the schema with
  best-effort placeholders, confidence "low", and the ambiguity under
  missingInfo.
- Output JSON only. No prose, no markdown fences, no preamble.
"""

# Bookkeeping keys never emitted in an advisory suggestion's fields dict —
# the assignment route stamps source/created_by itself and assignment
# records carry no needs_review flag.
_SUGGESTION_FIELD_EXCLUDES = ("needs_review", "source", "ai_raw_input", "ai_model")

# Per-target extras the classifier may emit (top-level or under "extras").
_CLASSIFY_EXTRA_KEYS = {
    "work_tasks": ("cad_skill_area", "requested_by", "request_reference", "due_date"),
    "project_work_tasks": ("project_number", "project_name", "engineer", "billing_phase"),
    "training_tasks": ("skill_area", "trainees", "requested_by", "due_date"),
    "personnel_issues": (
        "person_name", "observed_by", "cad_skill_area", "severity",
        "recommended_training", "project_number",
    ),
    "personal_items": ("due_date",),
}


def _classify_build_user_message(raw_text, hints):
    body = (raw_text or "").strip()
    lines = []
    for key, val in (hints or {}).items():
        sval = str(val).strip() if val is not None else ""
        if sval:
            lines.append(f"- {key}: {sval}")
    if not lines:
        return body
    parts = [body, "", "---", "OPERATOR HINTS (strong steers — trust these):"]
    parts.extend(lines)
    return "\n".join(parts)


def _classify_extract_presets(result, target):
    """Collect recognized per-target extras from the model output."""
    presets = {}
    extras = result.get("extras")
    sources = [extras if isinstance(extras, dict) else {}, result]
    for key in _CLASSIFY_EXTRA_KEYS.get(target, ()):
        for src in sources:
            val = src.get(key)
            sval = str(val).strip() if val is not None else ""
            if sval:
                presets[key] = sval
                break
    return presets


def _suggestion_fields_from_payload(target, payload):
    """Filter a payload down to the advisory suggestion's fields dict."""
    allowed = set(ALLOWED_TABLES[target]["fields"]) - set(_SUGGESTION_FIELD_EXCLUDES)
    return {k: v for k, v in payload.items() if k in allowed}


def _classify_normalize(result, raw_text, model):
    """Turn a raw classification model response into a suggestion dict.

    Returns the suggestion dict or None when the response is unusable
    (so the chain falls through to the next model).
    """
    if not isinstance(result, dict):
        return None
    target = str(result.get("target_table") or "").strip()
    if target not in TRIAGE_CLASSIFY_TARGETS:
        return None
    plan = _triage_normalize_plan(result)
    if not plan or not plan["gist"]:
        return None

    confidence = str(result.get("confidence") or "").strip().lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    rationale = str(result.get("rationale") or "").strip()[:200]

    presets = _classify_extract_presets(result, target)
    category = None
    if target == "personal_items":
        category = _normalize_internal_category(result.get("category"))
        presets["category"] = category

    payload = triage_plan_to_payload(plan, raw_text, model, target, presets)
    fields = _suggestion_fields_from_payload(target, payload)

    return {
        "target_table": target,
        "category": category,
        "confidence": confidence,
        "fields": fields,
        "model": model,
        "rationale": rationale,
    }


def run_classify(raw_text, hints=None):
    """Classify raw text into a target tracker + drafted fields.

    Returns (suggestion_dict, model_used) or raises RuntimeError.
    suggestion_dict matches the suggestion_json contract:
    {"target_table", "category" (personal_items only, else None),
     "confidence" ("high"|"medium"|"low"), "fields" (only keys valid for
     the target per ALLOWED_TABLES, no needs_review/source/ai_* keys),
     "model", "rationale" (<=200 chars)}.

    ADVISORY ONLY — callers persist this on the inbox item; they must
    never auto-create tracker records from it.
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise RuntimeError("empty input")
    user_message = _classify_build_user_message(raw_text, hints)

    errors = []
    for model in (TRIAGE_MODEL_LOCAL, TRIAGE_MODEL_CLOUD):
        if not model:
            continue
        try:
            result = _triage_call_model(
                model, user_message, system_prompt=TRIAGE_CLASSIFY_SYSTEM_PROMPT
            )
        except Exception as exc:  # noqa: BLE001 — record and try the next model
            errors.append(f"{model}: {exc}")
            continue
        suggestion = _classify_normalize(result, raw_text, model)
        if suggestion:
            return suggestion, model
        errors.append(f"{model}: unparseable classification")

    raise RuntimeError("classification chain exhausted — " + " | ".join(errors))


def suggestion_to_payload(suggestion, raw_text=""):
    """Render a stored suggestion into a row payload for create_direct_record.

    Used by the assignment flow at promote time. The returned payload
    carries NO needs_review flag (a human just reviewed the suggestion)
    and no source/ai_* bookkeeping — the caller stamps source and
    created_by_* itself. Raises ValueError on an unusable suggestion.
    """
    if not isinstance(suggestion, dict):
        raise ValueError("suggestion must be a dict")
    target = str(suggestion.get("target_table") or "").strip()
    if target not in TRIAGE_CLASSIFY_TARGETS:
        raise ValueError(f"unsupported target_table: {target}")

    raw_fields = suggestion.get("fields")
    if not isinstance(raw_fields, dict):
        raw_fields = {}
    allowed = set(ALLOWED_TABLES[target]["fields"]) - set(_SUGGESTION_FIELD_EXCLUDES)
    payload = {
        k: v for k, v in raw_fields.items()
        if k in allowed and v is not None and str(v).strip() != ""
    }

    stripped = (raw_text or "").strip()
    fallback_title = stripped.splitlines()[0][:120] if stripped else "Untitled intake"

    if target == "personal_items":
        payload["category"] = _normalize_internal_category(
            suggestion.get("category") or payload.get("category")
        )
        if not str(payload.get("title") or "").strip():
            payload["title"] = fallback_title
    elif target == "personnel_issues":
        if not str(payload.get("issue_description") or "").strip():
            payload["issue_description"] = fallback_title
    else:
        if not str(payload.get("title") or "").strip():
            payload["title"] = fallback_title

    if not str(payload.get("status") or "").strip():
        payload["status"] = ALLOWED_TABLES[target]["status_flow"][0]

    payload.pop("needs_review", None)  # belt & suspenders — never set here
    return payload


# Auto-detect helper exported for the route layer (used in response metadata).
auto_project_number = _triage_auto_project_number
