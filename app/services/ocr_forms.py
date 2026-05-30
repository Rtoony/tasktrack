"""OCR helpers for TaskTrack printable intake forms.

This deterministic parser reads stable labels from browser/PDF/reMarkable
forms, then builds a Dashboard Capture prefill. It does not create records.
"""
from __future__ import annotations

import re
from datetime import datetime

PRINTABLE_REQUEST_FORMS = [
    {
        "key": "project-work",
        "form_id": "TT-PROJECT-WORK-REQUEST",
        "title": "Project Work Request",
        "target_table": "project_work_tasks",
        "capture_target": "project_work_tasks",
        "source": "paper-form",
        "online_href": "/intake/request?type=project_work",
        "summary_label": "What project work is being requested?",
        "action_label": "Requested action / expected deliverable",
        "examples": "plan revision, submittal prep, exhibit update, agency response, project follow-up",
        "sections": [
            "Project number / project name",
            "Billing phase or task area",
            "Engineer / reviewer / requested by",
            "Due date or meeting date",
            "Background and constraints",
            "Definition of done",
        ],
    },
    {
        "key": "cad-development",
        "form_id": "TT-CAD-ISSUE-REQUEST",
        "title": "CAD / Detailing Issue Request",
        "target_table": "work_tasks",
        "capture_target": "work_tasks",
        "source": "paper-form",
        "online_href": "/intake/request?type=cad",
        "summary_label": "What CAD/detailing issue needs attention?",
        "action_label": "Requested fix, standard, or improvement",
        "examples": "Civil 3D issue, sheet/detail correction, template problem, plotting issue, Bluebeam markup",
        "sections": [
            "Project number, file, sheet, or detail reference",
            "Software involved",
            "Observed problem",
            "Impact / urgency",
            "Known workaround",
            "Screenshots, markups, or attachments referenced",
        ],
    },
    {
        "key": "training",
        "form_id": "TT-TRAINING-IMPROVEMENT-REQUEST",
        "title": "Training / Improvement Request",
        "target_table": "training_tasks",
        "capture_target": "training_tasks",
        "source": "paper-form",
        "online_href": "/intake/request?type=training",
        "summary_label": "What training, coaching, or improvement is needed?",
        "action_label": "Desired outcome",
        "examples": "standard clarification, repeated mistake, workflow improvement, software coaching, documentation request",
        "sections": [
            "Person/team affected",
            "Skill area or workflow",
            "Observed gap or opportunity",
            "Suggested training format",
            "Priority / timing",
            "How success should be measured",
        ],
    },
    {
        "key": "general-follow-up",
        "form_id": "TT-GENERAL-FOLLOW-UP",
        "title": "General Follow-Up / Operations Note",
        "target_table": "personal_items",
        "capture_target": "work_tasks",
        "source": "paper-form",
        "online_href": "/intake/request?type=general",
        "summary_label": "What needs follow-up?",
        "action_label": "Next action requested",
        "examples": "meeting follow-up, management question, office process, asset/equipment note, reminder",
        "sections": [
            "People involved",
            "Project or topic",
            "Question / decision needed",
            "Deadline or reminder date",
            "Notes / context",
            "Preferred follow-up method",
        ],
    },
]

FORM_BY_ID = {row["form_id"]: row for row in PRINTABLE_REQUEST_FORMS}
FORM_BY_TARGET = {row["target_table"]: row for row in PRINTABLE_REQUEST_FORMS}
OCR_LABELS = {
    "FORM_ID", "TT_FORM_ID", "TARGET_TABLE", "SOURCE", "REQUESTOR",
    "PROJECT_NUMBER", "PRIORITY", "DUE_DATE", "REQUEST_SUMMARY",
    "REQUESTED_ACTION", "FOLLOW_UP_QUESTIONS",
}
PROJECT_RE = re.compile(r"(?<!\d)(\d{4})[.\-\s]?(\d{2})(?!\d)")
LABEL_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_ /-]{1,40})\s*[:=]\s*(.*)\s*$")


def _norm_label(label: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(label or "").upper()).strip("_")


def _clean(value) -> str:
    return str(value or "").strip()


def _clean_project_number(value: str) -> str:
    text = _clean(value)
    match = PROJECT_RE.search(text)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return text[:64]


def _clean_priority(value: str) -> str:
    text = _clean(value).lower()
    if not text:
        return ""
    if "critical" in text or "urgent" in text or "high" in text:
        return "High"
    if "low" in text:
        return "Low"
    if "medium" in text or "normal" in text:
        return "Medium"
    return ""


def _clean_date(value: str) -> str:
    text = _clean(value)
    if not text:
        return ""
    iso = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if iso:
        y, m, d = iso.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    us = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2}|\d{2})\b", text)
    if us:
        m, d, y = us.groups()
        year = int(y) + 2000 if len(y) == 2 else int(y)
        return f"{year:04d}-{int(m):02d}-{int(d):02d}"
    try:
        return datetime.fromisoformat(text[:10]).date().isoformat()
    except ValueError:
        return text[:32]


def _extract_fields(text: str) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = LABEL_RE.match(line)
        if match:
            key = _norm_label(match.group(1))
            value = match.group(2).strip()
            if key in OCR_LABELS:
                current = key
                fields.setdefault(key, [])
                if value:
                    fields[key].append(value)
                continue
        if current and line:
            fields[current].append(line)
    return {key: "\n".join(value).strip() for key, value in fields.items()}


def _detect_form(fields: dict[str, str], text: str) -> dict | None:
    form_id = _clean(fields.get("FORM_ID") or fields.get("TT_FORM_ID")).upper()
    if form_id in FORM_BY_ID:
        return FORM_BY_ID[form_id]
    target = _clean(fields.get("TARGET_TABLE"))
    if target in FORM_BY_TARGET:
        return FORM_BY_TARGET[target]
    upper = text.upper()
    for candidate, form in FORM_BY_ID.items():
        if candidate in upper:
            return form
    return None


def _capture_text(*, form: dict | None, fields: dict[str, str], raw_text: str,
                  source_ref: str = "") -> str:
    lines: list[str] = []
    if form:
        lines.append(f"Paper intake form: {form['title']}")
        lines.append(f"FORM_ID: {form['form_id']}")
        lines.append(f"Target table: {form['target_table']}")
    if source_ref:
        lines.append(f"Source ref: {source_ref}")
    if fields.get("REQUESTOR"):
        lines.append(f"Requested by: {fields['REQUESTOR']}")
    if fields.get("PROJECT_NUMBER"):
        lines.append(f"Project: {_clean_project_number(fields['PROJECT_NUMBER'])}")
    if fields.get("PRIORITY"):
        lines.append(f"Priority: {fields['PRIORITY']}")
    if fields.get("DUE_DATE"):
        lines.append(f"Due date: {fields['DUE_DATE']}")
    if fields.get("REQUEST_SUMMARY"):
        lines.extend(["", "Request summary:", fields["REQUEST_SUMMARY"]])
    if fields.get("REQUESTED_ACTION"):
        lines.extend(["", "Requested action:", fields["REQUESTED_ACTION"]])
    if fields.get("FOLLOW_UP_QUESTIONS"):
        lines.extend(["", "Follow-up questions:", fields["FOLLOW_UP_QUESTIONS"]])
    lines.extend(["", "Original OCR text:", raw_text.strip()])
    return "\n".join(line for line in lines if line is not None).strip()



def _first_line(*values: str, fallback: str = "OCR intake request") -> str:
    for value in values:
        text = _clean(value)
        if text:
            return text.splitlines()[0].strip()[:180]
    return fallback


def _body_from_parsed(parsed: dict) -> str:
    lines: list[str] = []
    if parsed.get("request_summary"):
        lines.extend(["Request summary:", parsed["request_summary"], ""])
    if parsed.get("requested_action"):
        lines.extend(["Requested action:", parsed["requested_action"], ""])
    if parsed.get("follow_up_questions"):
        lines.extend(["Follow-up questions:", parsed["follow_up_questions"], ""])
    return "\n".join(lines).strip()


def printable_form_record_payload(parsed: dict, *, created_by_user_id=None,
                                  created_by_name: str = "") -> tuple[str, dict, str | None]:
    """Build a direct-create payload from parsed printable-form OCR.

    Returns ``(target_table, payload, error)``. The payload intentionally
    marks records for review because OCR can misread handwritten content.
    """
    if not parsed.get("detected"):
        return "", {}, "known TaskTrack FORM_ID is required for direct create"

    target = _clean(parsed.get("target_table") or parsed.get("capture_target"))
    if target not in {"work_tasks", "project_work_tasks", "training_tasks", "personal_items"}:
        return "", {}, f"direct create is not supported for {target or 'unknown target'}"

    form_title = _clean(parsed.get("form_title")) or "Printable intake form"
    source = _clean(parsed.get("source")) or "paper-form"
    form_id = _clean(parsed.get("form_id"))
    project_number = _clean(parsed.get("project_number"))
    requested_by = _clean(parsed.get("requested_by"))
    priority = _clean(parsed.get("priority")) or "Medium"
    due_date = _clean(parsed.get("due_date"))
    body = _body_from_parsed(parsed)
    raw_text = _clean((parsed.get("prefill") or {}).get("text")) or body
    title = _first_line(
        parsed.get("request_summary", ""),
        parsed.get("requested_action", ""),
        fallback=form_title,
    )

    common = {
        "source": source[:32],
        "created_by_user_id": created_by_user_id,
        "created_by_name": created_by_name or "",
    }

    if target == "project_work_tasks":
        if not project_number:
            return "", {}, "project_number is required to create a Project Task from this form"
        payload = {
            **common,
            "title": title,
            "project_number": project_number,
            "project_name": project_number,
            "engineer": requested_by,
            "task_description": body or raw_text,
            "priority": priority,
            "status": "Not Started",
            "due_at": f"{due_date}T17:00" if due_date else "",
            "notes": f"Created from {form_id or form_title}",
            "needs_review": 1,
            "ai_raw_input": raw_text[:8000],
            "ai_model": "ocr-form-parser",
        }
        return target, payload, None

    if target == "work_tasks":
        payload = {
            **common,
            "title": title,
            "cad_skill_area": "Paper intake",
            "description": body or raw_text,
            "requested_by": requested_by,
            "request_reference": form_id,
            "priority": priority,
            "status": "Not Started",
            "due_date": due_date,
            "notes": f"Created from {form_title}",
            "needs_review": 1,
            "ai_raw_input": raw_text[:8000],
            "ai_model": "ocr-form-parser",
            "project_number": project_number,
        }
        return target, payload, None

    if target == "training_tasks":
        payload = {
            **common,
            "title": title,
            "trainees": requested_by,
            "requested_by": requested_by,
            "skill_area": "Paper intake",
            "training_goals": body or raw_text,
            "additional_context": f"Created from {form_id or form_title}",
            "priority": priority,
            "status": "Not Started",
            "due_date": due_date,
            "notes": "",
            "needs_review": 1,
            "ai_raw_input": raw_text[:8000],
            "ai_model": "ocr-form-parser",
            "project_number": project_number,
        }
        return target, payload, None

    payload = {
        **common,
        "title": title,
        "category": "Follow-up",
        "body": body or raw_text,
        "priority": priority,
        "status": "New",
        "due_date": due_date,
        "source_ref": form_id,
    }
    return target, payload, None

def parse_printable_form_ocr(text: str, *, source_ref: str = "") -> dict:
    """Parse OCR text from the printable packet into a capture prefill."""
    raw_text = _clean(text)
    fields = _extract_fields(raw_text)
    form = _detect_form(fields, raw_text)
    project_number = _clean_project_number(fields.get("PROJECT_NUMBER") or raw_text)
    if project_number and not PROJECT_RE.fullmatch(project_number):
        detected = _clean_project_number(raw_text)
        project_number = detected if PROJECT_RE.fullmatch(detected) else ""

    source = _clean(fields.get("SOURCE")) or (form or {}).get("source") or "remarkable-ocr"
    target_table = (form or {}).get("target_table") or _clean(fields.get("TARGET_TABLE")) or "work_tasks"
    capture_target = (form or {}).get("capture_target") or target_table
    if capture_target not in {"work_tasks", "project_work_tasks", "training_tasks"}:
        capture_target = "work_tasks"

    priority = _clean_priority(fields.get("PRIORITY", ""))
    due_date = _clean_date(fields.get("DUE_DATE", ""))
    requested_by = _clean(fields.get("REQUESTOR", ""))[:120]
    summary = _clean(fields.get("REQUEST_SUMMARY", ""))
    action = _clean(fields.get("REQUESTED_ACTION", ""))
    questions = _clean(fields.get("FOLLOW_UP_QUESTIONS", ""))
    warnings: list[str] = []
    if target_table != capture_target:
        warnings.append(f"{target_table} is not an AI triage target yet; routing OCR capture to {capture_target}.")
    if not form:
        warnings.append("No known TaskTrack FORM_ID detected; using generic OCR capture.")

    score = 0.2
    if form:
        score += 0.45
    if fields.get("TARGET_TABLE"):
        score += 0.1
    if summary or action:
        score += 0.15
    if project_number or requested_by or due_date:
        score += 0.1
    confidence = min(0.99, round(score, 2))

    capture_text = _capture_text(form=form, fields=fields, raw_text=raw_text, source_ref=source_ref)
    prefill = {
        "source": source[:32],
        "target": capture_target,
        "project_number": project_number,
        "requested_by": requested_by,
        "priority": priority,
        "due_date": due_date,
        "text": capture_text,
    }
    prefill = {key: value for key, value in prefill.items() if value not in (None, "")}
    return {
        "detected": bool(form),
        "confidence": confidence,
        "form_id": (form or {}).get("form_id") or _clean(fields.get("FORM_ID") or fields.get("TT_FORM_ID")),
        "form_key": (form or {}).get("key") or "",
        "form_title": (form or {}).get("title") or "Generic OCR Capture",
        "target_table": target_table,
        "capture_target": capture_target,
        "source": source[:32],
        "project_number": project_number,
        "requested_by": requested_by,
        "priority": priority,
        "due_date": due_date,
        "request_summary": summary,
        "requested_action": action,
        "follow_up_questions": questions,
        "fields": fields,
        "prefill": prefill,
        "warnings": warnings,
    }


__all__ = [
    "PRINTABLE_REQUEST_FORMS",
    "parse_printable_form_ocr",
    "printable_form_record_payload",
]
