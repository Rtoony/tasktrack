"""Ticket data helpers shared across blueprints.

Validation, status semantics, the weekly-submission row parser, and the
common "create a record + log it" path. These take a SQLAlchemy session
plus a payload — no Flask routing concerns here.
"""
import re
from datetime import date, datetime

from flask import session
from sqlalchemy.orm import Session

from ..config import ALLOWED_TABLES
from ..models import (
    InboxItem, PersonnelIssue, ProjectWorkTask, Suggestion, TrainingTask, WorkTask,
)
from .audit import log_activity

TABLE_MODELS = {
    "work_tasks": WorkTask,
    "project_work_tasks": ProjectWorkTask,
    "training_tasks": TrainingTask,
    "personnel_issues": PersonnelIssue,
    "suggestion_box": Suggestion,
    "inbox_items": InboxItem,
}


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
    if table_name == "suggestion_box":
        return {"Promoted to CAD", "Declined"}
    if table_name == "inbox_items":
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
    if table == "suggestion_box":
        for key in ("title", "suggestion_type", "submitted_by", "submitted_for", "summary", "expected_value", "review_notes"):
            if key in data and data.get(key) is not None:
                data[key] = str(data.get(key) or "").strip()
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
    log_activity(sess, table, record.id, action,
                 new=action_detail or source_name)
    return record.id, None
