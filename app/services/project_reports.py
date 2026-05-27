"""Project status and portfolio report data builders."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
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

DEFAULT_PORTFOLIO_LIMIT = 12
MAX_PORTFOLIO_LIMIT = 50


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


def _clamp_limit(raw, default: int = DEFAULT_PORTFOLIO_LIMIT) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_PORTFOLIO_LIMIT))


def _clean_project_numbers(values: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for part in str(value or "").replace("\n", ",").split(","):
            item = part.strip()
            if item and item not in out:
                out.append(item)
    return out


def project_status_report(sess: Session, *, project_id: int | None = None,
                          project_number: str = "",
                          user_id: int | None = None,
                          include_private: bool = False,
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
    workspace_user_id = user_id if include_private else None
    workspace = project_workspace_payload(sess, proj, user_id=workspace_user_id)
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


def _portfolio_project_stmt(filters: dict):
    stmt = select(Project)
    if not filters.get("include_inactive"):
        stmt = stmt.where(Project.active == 1)

    project_numbers = _clean_project_numbers(filters.get("project_numbers") or [])
    if project_numbers:
        stmt = stmt.where(Project.project_number.in_(project_numbers))

    q = (filters.get("q") or "").strip()
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(
            Project.project_number.ilike(pattern),
            Project.name.ilike(pattern),
            Project.client.ilike(pattern),
            Project.component.ilike(pattern),
            Project.principal.ilike(pattern),
        ))

    client = (filters.get("client") or "").strip()
    if client:
        stmt = stmt.where(Project.client.ilike(f"%{client}%"))

    principal = (filters.get("principal") or "").strip()
    if principal:
        stmt = stmt.where(Project.principal.ilike(f"%{principal}%"))

    component = (filters.get("component") or "").strip()
    if component:
        stmt = stmt.where(Project.component == component)

    display_status = (filters.get("display_status") or "").strip()
    if display_status:
        stmt = stmt.where(Project.display_status == display_status)

    return stmt.order_by(Project.project_number.asc())


def _portfolio_summary(reports: list[dict]) -> dict:
    counts = {key: 0 for key in REPORT_TABLES}
    open_counts = {key: 0 for key in REPORT_TABLES}
    site_count = 0
    overdue_count = 0
    upcoming_count = 0
    for report in reports:
        site_count += int(report.get("counts", {}).get("sites") or 0)
        overdue_count += len(report.get("overdue_items") or [])
        upcoming_count += len(report.get("upcoming_events") or [])
        for key in REPORT_TABLES:
            counts[key] += int(report.get("counts", {}).get(key) or 0)
            open_counts[key] += int(report.get("open_counts", {}).get(key) or 0)
    return {
        "project_count": len(reports),
        "site_count": site_count,
        "record_counts": counts,
        "open_counts": open_counts,
        "overdue_count": overdue_count,
        "upcoming_event_count": upcoming_count,
    }


def portfolio_project_report(sess: Session, *, filters: dict | None = None,
                             user_id: int | None = None,
                             include_private: bool = False,
                             now: datetime | None = None) -> dict:
    """Build a print-friendly packet across multiple filtered projects."""
    filters = dict(filters or {})
    limit = _clamp_limit(filters.get("limit"))
    now = now or datetime.now()

    rows = sess.scalars(_portfolio_project_stmt(filters).limit(limit + 1)).all()
    truncated = len(rows) > limit
    projects = rows[:limit]
    reports = [
        project_status_report(
            sess,
            project_id=proj.id,
            user_id=user_id,
            include_private=include_private,
            now=now,
        )
        for proj in projects
    ]
    reports = [report for report in reports if report is not None]

    safe_filters = {
        "q": (filters.get("q") or "").strip(),
        "project_numbers": _clean_project_numbers(filters.get("project_numbers") or []),
        "client": (filters.get("client") or "").strip(),
        "principal": (filters.get("principal") or "").strip(),
        "component": (filters.get("component") or "").strip(),
        "display_status": (filters.get("display_status") or "").strip(),
        "include_inactive": bool(filters.get("include_inactive")),
        "limit": limit,
    }
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "filters": safe_filters,
        "limit": limit,
        "truncated": truncated,
        "reports": reports,
        "summary": _portfolio_summary(reports),
        "labels": REPORT_TABLES,
        "include_private": bool(include_private),
    }


__all__ = [
    "project_status_report",
    "portfolio_project_report",
    "REPORT_TABLES",
    "DEFAULT_PORTFOLIO_LIMIT",
    "MAX_PORTFOLIO_LIMIT",
]
