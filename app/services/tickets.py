"""Ticket data helpers shared across blueprints.

Validation, status semantics, the weekly-submission row parser, and the
common "create a record + log it" path. These take a SQLAlchemy session
plus a payload — no Flask routing concerns here.
"""
import json
import re
from datetime import date, datetime

from flask import session
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import (
    ALLOWED_TABLES,
    CALENDAR_EVENT_TYPES,
    CALENDAR_VISIBILITIES,
    INTERNAL_ITEM_CATEGORIES,
)
from ..models import (
    CalendarEvent,
    Employee,
    FeedbackItem,
    InboxItem,
    PersonalItem,
    PersonnelIssue,
    Project,
    to_dict,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
)
from .audit import log_activity

TABLE_MODELS = {
    "work_tasks": WorkTask,
    "project_work_tasks": ProjectWorkTask,
    "training_tasks": TrainingTask,
    "personnel_issues": PersonnelIssue,
    "inbox_items": InboxItem,
    "feedback_items": FeedbackItem,
    "personal_items": PersonalItem,
    "calendar_events": CalendarEvent,
}

# Phase-0 FK spine: per-tracker mapping from text column → (FK column,
# Employee|Project, lookup column). Used by enrich_with_fks() to do
# exact-name lookups after the AI/intake writes text values.
_FK_ENRICHMENT = {
    "work_tasks": [
        ("project_number", "project_id", Project, "project_number"),
    ],
    "project_work_tasks": [
        ("project_number", "project_id", Project, "project_number"),
        ("engineer", "engineer_id", Employee, "display_name"),
    ],
    "training_tasks": [
        ("project_number", "project_id", Project, "project_number"),
    ],
    "personnel_issues": [
        ("project_number", "project_id", Project, "project_number"),
        ("person_name", "person_id", Employee, "display_name"),
    ],
    "calendar_events": [
        ("project_number", "project_id", Project, "project_number"),
    ],
}


def _coerce_fk_columns(data):
    """Strip empty strings and coerce numeric strings on FK columns.

    Browsers (and the AI triage path) sometimes submit "" for an empty
    select; SQLite happily stores that as the literal string "" which
    later breaks comparison with the integer id. Normalise to None or int.
    """
    for key in ("project_id", "engineer_id", "person_id", "related_id"):
        if key not in data:
            continue
        raw = data[key]
        if raw in (None, "", "null"):
            data[key] = None
            continue
        try:
            data[key] = int(raw)
        except (TypeError, ValueError):
            data[key] = None


def enrich_with_fks(sess: Session, table: str, record, *,
                    refresh_existing: bool = False,
                    changed_fields: set[str] | None = None) -> bool:
    """Best-effort: populate FK columns from matching text fields.

    Exact-match only (no fuzzy). By default, skips columns that already
    have a value. On update, callers can pass ``refresh_existing=True``
    plus ``changed_fields`` so changing a text key like project_number
    refreshes the paired FK while leaving explicit FK edits alone.

    Special-cases personnel_issues' `person_name` field: it may carry a
    comma-separated list of names, in which case we populate the
    multi-FK `person_ids` JSON array (Phase 5.5) AND set `person_id`
    to the first match for backward compat.
    """
    changed = False
    mapping = _FK_ENRICHMENT.get(table)
    if mapping:
        for text_col, fk_col, model, lookup_col in mapping:
            # Skip person_id here — handled in the multi-person block below
            # so we don't double-write or leave person_ids stale.
            if table == "personnel_issues" and text_col == "person_name":
                continue
            if changed_fields is not None:
                if text_col not in changed_fields or fk_col in changed_fields:
                    continue
            if not refresh_existing and getattr(record, fk_col, None):
                continue
            text_val = (getattr(record, text_col, "") or "").strip()
            if not text_val:
                if refresh_existing and getattr(record, fk_col, None) is not None:
                    setattr(record, fk_col, None)
                    changed = True
                continue
            hit = sess.scalar(
                select(model).where(
                    func.lower(getattr(model, lookup_col)) == text_val.lower()
                ).limit(1)
            )
            new_fk = hit.id if hit is not None else None
            if getattr(record, fk_col, None) != new_fk:
                setattr(record, fk_col, new_fk)
                changed = True

    # Phase-5.5 multi-person path for personnel_issues.
    if table == "personnel_issues" and isinstance(record, PersonnelIssue):
        if _resolve_person_ids(sess, record):
            changed = True

    return changed


def _resolve_person_ids(sess: Session, record: PersonnelIssue) -> bool:
    """Split person_name on commas, resolve each name to an Employee.id,
    write the result as a JSON list into person_ids. Also seeds person_id
    from the first match if it isn't already set. Idempotent.

    Treats `person_ids` already on the record as authoritative — if the
    caller (modal UI with multi-select) supplied a valid JSON list, we
    keep it and skip the text-parse step entirely. This way the UI's
    explicit selection beats the comma-parse fallback.
    """
    raw_ids = getattr(record, "person_ids", "") or ""
    # If caller already supplied a non-empty list, treat as authoritative.
    try:
        if raw_ids and raw_ids.strip().startswith("["):
            existing = json.loads(raw_ids)
            if isinstance(existing, list) and existing:
                # Just make sure person_id mirrors first entry.
                first = existing[0]
                try:
                    first_int = int(first)
                except (TypeError, ValueError):
                    first_int = None
                if first_int and not record.person_id:
                    record.person_id = first_int
                    return True
                return False
    except (json.JSONDecodeError, ValueError):
        pass  # fall through to comma-split

    text = (record.person_name or "").strip()
    if not text:
        # Make sure the column has a valid JSON empty list rather than '' / NULL.
        if raw_ids != "[]":
            record.person_ids = "[]"
            return True
        return False

    # Split on comma OR semicolon — both common in human input.
    names = [n.strip() for n in re.split(r"[,;]", text) if n.strip()]
    if not names:
        return False
    found_ids: list[int] = []
    for name in names:
        hit = sess.scalar(
            select(Employee).where(
                func.lower(Employee.display_name) == name.lower()
            ).limit(1)
        )
        if hit is not None and hit.id not in found_ids:
            found_ids.append(hit.id)
    new_value = json.dumps(found_ids)
    changed = (new_value != raw_ids)
    record.person_ids = new_value
    if found_ids and not record.person_id:
        record.person_id = found_ids[0]
        changed = True
    return changed


def overdue_field_for_table(cfg):
    if "due_at" in cfg["fields"]:
        return "due_at"
    if "follow_up_date" in cfg["fields"]:
        return "follow_up_date"
    if "due_date" in cfg["fields"]:
        return "due_date"
    return None


def done_statuses_for_table(table_name):
    if table_name == "personnel_issues":
        return {"Closed"}
    if table_name in ("inbox_items", "personal_items"):
        return {"Done", "Archived"}
    if table_name == "calendar_events":
        return {"done", "cancelled"}
    if table_name == "feedback_items":
        return {"Fixed", "Closed", "Won\'t Fix"}
    return {"Complete"}


def record_visible_to_user(table_name, row, user_id) -> bool:
    """Shared row-presence rule, currently for private calendar events."""
    if table_name != "calendar_events":
        return True
    if getattr(row, "visibility", "") != "private":
        return True
    return user_id is not None and getattr(row, "created_by_user_id", None) == user_id


def _is_owner(row, user_id) -> bool:
    return user_id is not None and getattr(row, "created_by_user_id", None) == user_id


def can_view_record_detail(table_name, row, user_id, *, is_admin: bool = False) -> bool:
    """Whether the caller may receive full-detail JSON for a record."""
    if row is None or not record_visible_to_user(table_name, row, user_id):
        return False
    if table_name == "personnel_issues":
        return bool(is_admin or _is_owner(row, user_id))
    return True


def redacted_record_dict(table_name, row) -> dict:
    """Return metadata-only JSON for sensitive rows on summary surfaces."""
    if table_name != "personnel_issues":
        return to_dict(row) or {}
    return {
        "id": getattr(row, "id", None),
        "title": "Capability note (restricted)",
        "status": getattr(row, "status", "") or "",
        "severity": getattr(row, "severity", "") or "",
        "reported_date": str(getattr(row, "reported_date", "") or ""),
        "follow_up_date": getattr(row, "follow_up_date", "") or "",
        "estimated_time_loss_minutes": getattr(row, "estimated_time_loss_minutes", 0) or 0,
        "project_id": getattr(row, "project_id", None),
        "project_number": getattr(row, "project_number", "") or "",
        "redacted": True,
    }


def record_to_user_dict(table_name, row, user_id, *, is_admin: bool = False) -> dict:
    if can_view_record_detail(table_name, row, user_id, is_admin=is_admin):
        return to_dict(row) or {}
    return redacted_record_dict(table_name, row)


def is_overdue_value(raw_value):
    if not raw_value:
        return False

    value = str(raw_value).strip()
    if not value:
        return False

    try:
        if "T" in value:
            return datetime.fromisoformat(value) < datetime.now()
        return datetime.fromisoformat(value).date() < date.today()
    except ValueError:
        return False



def _validate_iso_datetime_field(data, key: str, label: str, *, required: bool = False):
    raw = str(data.get(key) or "").strip()
    if not raw:
        if required:
            return f"'{key}' is required", None
        data[key] = ""
        return None, None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return f"{label} must be a valid ISO date or datetime", None
    data[key] = raw
    return None, parsed


def _validate_calendar_event(data, creating=False):
    if creating or "title" in data:
        title = str(data.get("title") or "").strip()
        if not title:
            return "'title' is required"
        data["title"] = title

    if creating or "event_type" in data:
        event_type = str(data.get("event_type") or "meeting").strip()
        if event_type not in CALENDAR_EVENT_TYPES:
            return f"event_type must be one of: {', '.join(CALENDAR_EVENT_TYPES)}"
        data["event_type"] = event_type

    if creating or "status" in data:
        status = str(data.get("status") or ALLOWED_TABLES["calendar_events"]["status_flow"][0]).strip()
        if status not in ALLOWED_TABLES["calendar_events"]["status_flow"]:
            return f"status must be one of: {', '.join(ALLOWED_TABLES['calendar_events']['status_flow'])}"
        data["status"] = status

    if creating or "visibility" in data:
        visibility = str(data.get("visibility") or "internal").strip()
        if visibility not in CALENDAR_VISIBILITIES:
            return f"visibility must be one of: {', '.join(CALENDAR_VISIBILITIES)}"
        data["visibility"] = visibility

    start_error, start_dt = _validate_iso_datetime_field(
        data, "start_at", "Start", required=creating or "start_at" in data,
    )
    if start_error:
        return start_error

    end_dt = None
    if "end_at" in data:
        end_error, end_dt = _validate_iso_datetime_field(data, "end_at", "End")
        if end_error:
            return end_error

    if "reminder_date" in data:
        reminder_error, _ = _validate_iso_datetime_field(data, "reminder_date", "Reminder")
        if reminder_error:
            return reminder_error

    if start_dt is not None and end_dt is not None and end_dt < start_dt:
        return "end_at must be after start_at"

    if "all_day" in data:
        raw = data.get("all_day")
        if isinstance(raw, bool):
            data["all_day"] = 1 if raw else 0
        else:
            try:
                data["all_day"] = 1 if int(raw) else 0
            except (TypeError, ValueError):
                return "all_day must be 0 or 1"

    if "project_number" in data:
        data["project_number"] = str(data.get("project_number") or "").strip()
    if "related_table" in data:
        related_table = str(data.get("related_table") or "").strip()
        if related_table and related_table not in ALLOWED_TABLES:
            return "related_table must reference a known tracker table"
        data["related_table"] = related_table
    return None

def validate_record_data(table, data, creating=False):
    # Phase-0: normalize FK columns regardless of table.
    _coerce_fk_columns(data)

    if table == "personal_items":
        if creating or "category" in data:
            category = str(data.get("category") or "").strip()
            if not category:
                return "'category' is required"
            if category not in INTERNAL_ITEM_CATEGORIES:
                return f"category must be one of: {', '.join(INTERNAL_ITEM_CATEGORIES)}"
            data["category"] = category
        return None

    if table == "calendar_events":
        return _validate_calendar_event(data, creating)

    if table == "feedback_items":
        if creating or "title" in data:
            title = str(data.get("title") or "").strip()
            if not title:
                return "'title' is required"
            data["title"] = title
        for key in ("feedback_type", "priority", "status", "source"):
            if key in data:
                data[key] = str(data.get(key) or "").strip()
        if creating or "status" in data:
            status = str(data.get("status") or ALLOWED_TABLES["feedback_items"]["status_flow"][0]).strip()
            if status not in ALLOWED_TABLES["feedback_items"]["status_flow"]:
                return f"status must be one of: {', '.join(ALLOWED_TABLES['feedback_items']['status_flow'])}"
            data["status"] = status
        if "context_json" in data:
            raw_context = str(data.get("context_json") or "{}").strip() or "{}"
            try:
                json.loads(raw_context)
            except json.JSONDecodeError:
                return "context_json must be valid JSON"
            data["context_json"] = raw_context
        return None

    if table != "project_work_tasks":
        return None

    project_number = (data.get("project_number") or "").strip()
    project_name = (data.get("project_name") or "").strip()
    billing_phase = (data.get("billing_phase") or "").strip()
    engineer = (data.get("engineer") or "").strip()
    task_description = (data.get("task_description") or "").strip()
    due_at = (data.get("due_at") or "").strip()

    if creating or "project_name" in data:
        if not project_name:
            return "'project_name' is required"
        data["project_name"] = project_name

    if creating or "project_number" in data:
        if not project_number:
            return "'project_number' is required"
        if not re.fullmatch(r"\d{4}\.\d{2}", project_number):
            return "Project Number must match ####.##"
        data["project_number"] = project_number

    if billing_phase:
        if not re.fullmatch(r"\d{2}", billing_phase):
            return "Project Billing Phase must match ##"
        data["billing_phase"] = billing_phase

    if creating or "engineer" in data:
        data["engineer"] = engineer

    if creating or "task_description" in data:
        if not task_description:
            return "'task_description' is required"
        data["task_description"] = task_description

    if due_at:
        try:
            datetime.fromisoformat(due_at)
        except ValueError:
            return "Due date and time must be a valid datetime"
        data["due_at"] = due_at

    return None


def extra_create_fields(table, data):
    extras = {
        "created_by_user_id": session.get("user_id"),
        "created_by_name": session.get("user_name", ""),
    }
    if "status" not in data or not str(data.get("status", "")).strip():
        extras["status"] = ALLOWED_TABLES[table]["status_flow"][0]
    return extras


def build_weekly_submission_rows(form=None, min_rows=4):
    field_names = [
        "project_number[]",
        "project_name[]",
        "title[]",
        "task_description[]",
        "billing_phase[]",
        "engineer[]",
        "due_at[]",
    ]
    if not form:
        return [{} for _ in range(min_rows)]

    values = {name: form.getlist(name) for name in field_names}
    row_count = max((len(items) for items in values.values()), default=0)
    row_count = max(row_count, min_rows)
    rows = []
    for idx in range(row_count):
        rows.append({
            "project_number": (values["project_number[]"][idx] if idx < len(values["project_number[]"]) else "").strip(),
            "project_name": (values["project_name[]"][idx] if idx < len(values["project_name[]"]) else "").strip(),
            "title": (values["title[]"][idx] if idx < len(values["title[]"]) else "").strip(),
            "task_description": (values["task_description[]"][idx] if idx < len(values["task_description[]"]) else "").strip(),
            "billing_phase": (values["billing_phase[]"][idx] if idx < len(values["billing_phase[]"]) else "").strip(),
            "engineer": (values["engineer[]"][idx] if idx < len(values["engineer[]"]) else "").strip(),
            "due_at": (values["due_at[]"][idx] if idx < len(values["due_at[]"]) else "").strip(),
        })
    return rows


def create_direct_record(sess: Session, table, payload, source_name,
                         action="submitted", action_detail=""):
    """Insert a row + write an activity log entry within the caller's session.

    Returns (record_id, error_string_or_None). Caller commits or rolls
    back the session.
    """
    error = validate_record_data(table, payload, creating=True)
    if error:
        return None, error

    cfg = ALLOWED_TABLES[table]
    for req in cfg["required"]:
        if not str(payload.get(req, "")).strip():
            return None, f"'{req}' is required"

    Model = TABLE_MODELS.get(table)
    if Model is None:
        return None, f"unknown ticket table: {table}"
    valid_cols = {c.name for c in Model.__table__.columns}
    kwargs = {k: v for k, v in payload.items() if k in valid_cols}
    record = Model(**kwargs)
    sess.add(record)
    sess.flush()  # populate record.id without committing
    # Best-effort FK enrichment: if the caller provided text fields but no
    # FK ids, try to resolve them now. Quiet on miss; never blocks insert.
    enrich_with_fks(sess, table, record)
    log_activity(sess, table, record.id, action,
                 new=action_detail or source_name)
    return record.id, None
