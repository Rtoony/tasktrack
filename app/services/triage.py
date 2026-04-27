"""AI Intake (triage) — converts messy input into a structured ActionPlan.

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

import requests

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
- Output JSON only. No prose, no markdown fences, no preamble.
"""

TRIAGE_ALLOWED_TARGETS = ("work_tasks", "project_work_tasks", "training_tasks")
TRIAGE_TARGET_LABELS = {
    "work_tasks": "CAD Development",
    "project_work_tasks": "Project Work",
    "training_tasks": "Training",
}

TRIAGE_PRESET_KEYS = (
    "priority",
    "cad_skill_area",
    "skill_area",
    "requested_by",
    "request_reference",
    "due_date",
    "due_at",
    "notes",
    "project_number",
    "project_name",
    "billing_phase",
    "engineer",
    "trainees",
    "source",
)

TRIAGE_CONFIRM_TABLES = {"work_tasks", "project_work_tasks", "training_tasks"}


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


def _triage_call_model(model, raw_text):
    headers = {"Content-Type": "application/json"}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        f"{LITELLM_BASE_URL.rstrip('/')}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=TRIAGE_TIMEOUT_S,
    )
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
        for key in ("billing_phase", "engineer", "due_at", "notes"):
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

    else:
        raise ValueError(f"unsupported target_table: {target}")

    payload.update(common_ai)
    return payload


# Auto-detect helper exported for the route layer (used in response metadata).
auto_project_number = _triage_auto_project_number
