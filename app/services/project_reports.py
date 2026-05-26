"""Project status report data builders."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Project
from .project_workspace import project_workspace_payload
from .tickets import done_statuses_for_table, is_overdue_value, overdue_field_for_table

REPORT_TABLES = {
    "project_work_tasks": "Project Tasks",
    "calendar_events": "Calendar",
    "work_tasks": "CAD Dev",
    "training_tasks": "Training",
    "personnel_issues": "Capabilities",
}


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _record_title(table: str, row: dict) -> str:
    if table == "personnel_issues":
        return row.get("issue_description") or row.get("person_name") or f"#{row.get('id', '?')}"
    return row.get("title") or row.get("project_name") or f"#{row.get('id', '?')}"


def _record_when(table: str, row: dict) -> str:
    if table == "calendar_events":
        return row.get("start_at") or ""
    return row.get("due_at") or row.get("due_date") or row.get("follow_up_date") or ""


def _status(row: dict) -> str:
    return str(row.get("status") or "")


def _is_open(table: str, row: dict) -> bool:
    return _status(row) not in done_statuses_for_table(table)


def _overdue_item(table: str, row: dict) -> dict | None:
    if table == "calendar_events" or not _is_open(table, row):
        return None
    due_field = overdue_field_for_table({"fields": row.keys()})
    if not due_field or not is_overdue_value(row.get(due_field)):
        return None
    return {
        "table": table,
        "label": REPORT_TABLES.get(table, table),
        "id": row.get("id"),
        "title": _record_title(table, row),
        "status": _status(row),
        "due": row.get(due_field) or "",
    }


def _upcoming_calendar(rows: list[dict], now: datetime) -> list[dict]:
    out = []
    for row in rows:
        if _status(row) in done_statuses_for_table("calendar_events"):
            continue
        start = _parse_dt(row.get("start_at"))
        if start is None or start < now:
            continue
        out.append({
            "id": row.get("id"),
            "title": row.get("title") or "",
            "event_type": row.get("event_type") or "event",
            "status": _status(row),
            "start_at": row.get("start_at") or "",
            "location": row.get("location") or "",
            "visibility": row.get("visibility") or "",
        })
    out.sort(key=lambda r: r.get("start_at") or "")
    return out


def project_status_report(sess: Session, *, project_id: int | None = None,
                          project_number: str = "",
                          user_id: int | None = None,
                          now: datetime | None = None) -> dict | None:
    """Build a single-project management report from the workspace payload."""
    if project_id is not None:
        proj = sess.get(Project, project_id)
    elif project_number:
        proj = sess.scalar(select(Project).where(Project.project_number == project_number))
    else:
        return None
    if proj is None:
        return None

    now = now or datetime.now()
    workspace = project_workspace_payload(sess, proj, user_id=user_id)
    linked = workspace["linked_records"]

    open_counts = {}
    overdue = []
    for table, rows in linked.items():
        open_counts[table] = len([row for row in rows if _is_open(table, row)])
        for row in rows:
            item = _overdue_item(table, row)
            if item is not None:
                overdue.append(item)
    overdue.sort(key=lambda r: r.get("due") or "")

    upcoming = _upcoming_calendar(linked.get("calendar_events", []), now)

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "project": workspace["project"],
        "external": workspace["external"],
        "sites": workspace["sites"],
        "counts": workspace["counts"],
        "open_counts": open_counts,
        "overdue_items": overdue,
        "upcoming_events": upcoming[:12],
        "linked_records": linked,
        "labels": REPORT_TABLES,
    }


__all__ = ["project_status_report", "REPORT_TABLES"]
