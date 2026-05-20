"""Ticket data helpers shared across blueprints.

Validation, status semantics, the weekly-submission row parser, and the
common "create a record + log it" path. These take a SQLAlchemy session
plus a payload — no Flask routing concerns here.
"""
import re
from datetime import date, datetime

from flask import session
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import ALLOWED_TABLES, PERSONAL_CATEGORIES
from ..models import (
    Employee,
    InboxItem,
    PersonalItem,
    PersonnelIssue,
    Project,
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
    "personal_items": PersonalItem,
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
}


def _coerce_fk_columns(data):
    """Strip empty strings and coerce numeric strings on FK columns.

    Browsers (and the AI triage path) sometimes submit "" for an empty
    select; SQLite happily stores that as the literal string "" which
    later breaks comparison with the integer id. Normalise to None or int.
    """
    for key in ("project_id", "engineer_id", "person_id"):
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


def enrich_with_fks(sess: Session, table: str, record) -> bool:
    """Best-effort: populate empty FK columns from the row's text fields.

    Exact-match only (no fuzzy). Skips columns that already have a value.
    Returns True if anything changed.

    Called once after `create_direct_record` so AI triage / intake forms
    that only know how to write text still get FK enrichment.
    """
    mapping = _FK_ENRICHMENT.get(table)
    if not mapping:
        return False
    changed = False
    for text_col, fk_col, model, lookup_col in mapping:
        if getattr(record, fk_col, None):
            continue
        text_val = (getattr(record, text_col, "") or "").strip()
        if not text_val:
            continue
        hit = sess.scalar(
            select(model).where(
                func.lower(getattr(model, lookup_col)) == text_val.lower()
            ).limit(1)
        )
        if hit is not None:
            setattr(record, fk_col, hit.id)
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
    return {"Complete"}


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


def validate_record_data(table, data, creating=False):
    # Phase-0: normalize FK columns regardless of table.
    _coerce_fk_columns(data)

    if table == "personal_items":
        if creating or "category" in data:
            category = str(data.get("category") or "").strip()
            if not category:
                return "'category' is required"
            if category not in PERSONAL_CATEGORIES:
                return f"category must be one of: {', '.join(PERSONAL_CATEGORIES)}"
            data["category"] = category
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
