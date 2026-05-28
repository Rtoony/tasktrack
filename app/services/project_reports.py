"""Project status and portfolio report data builders."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import CalendarEvent, Project, to_dict
from .project_workspace import project_workspace_payload, recent_activity_for_linked_records
from .tickets import (
    done_statuses_for_table,
    is_overdue_value,
    overdue_field_for_table,
    record_visible_to_user,
)

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
        return (
            row.get("title")
            or row.get("issue_description")
            or row.get("person_name")
            or f"#{row.get('id', '?')}"
        )
    return row.get("title") or row.get("project_name") or f"#{row.get('id', '?')}"


def _calendar_event_payload(row: CalendarEvent) -> dict:
    payload = to_dict(row) or {}
    payload.update({
        "type": payload.get("event_type") or "",
        "start": payload.get("start_at") or "",
        "end": payload.get("end_at") or "",
        "all_day": bool(payload.get("all_day")),
        "location": payload.get("location") or "",
        "description": payload.get("description") or "",
        "project_number": payload.get("project_number") or "",
        "related_table": payload.get("related_table") or "",
        "reminder_date": payload.get("reminder_date") or "",
    })
    return payload


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


def _recent_activity_for_project(sess: Session, linked_records: dict[str, list[dict]],
                                 *, is_admin: bool = False,
                                 limit: int = 20) -> list[dict]:
    return recent_activity_for_linked_records(
        sess, linked_records, is_admin=is_admin, limit=limit
    )


def _project_open_total(report: dict) -> int:
    return sum(int(value or 0) for value in (report.get("open_counts") or {}).values())


def _plural(value: int, singular: str, plural: str | None = None) -> str:
    return f"{value} {singular if value == 1 else (plural or singular + 's')}"


def _project_management_brief(report: dict) -> dict:
    overdue = list(report.get("overdue_items") or [])
    upcoming = list(report.get("upcoming_events") or [])
    open_count = _project_open_total(report)
    next_event = upcoming[0] if upcoming else None

    if overdue:
        attention_level = "at_risk"
        headline = (
            f"{_plural(len(overdue), 'overdue linked item')} needs attention; "
            f"{_plural(open_count, 'open linked item')} remain open."
        )
        recommendation = "Review the overdue list first, then assign a next action or new date."
    elif next_event:
        attention_level = "scheduled"
        headline = f"Next project touchpoint: {next_event.get('title') or 'Untitled event'} on {(next_event.get('start_at') or '')[:16]}."
        recommendation = "Use the upcoming event as the prep anchor for project follow-up."
    elif open_count:
        attention_level = "active"
        headline = f"{_plural(open_count, 'open linked item')} remain active with no overdue linked work."
        recommendation = "Keep the project moving by selecting the highest-leverage next action."
    else:
        attention_level = "quiet"
        headline = "No open linked work, overdue items, or upcoming events are currently visible."
        recommendation = "No immediate follow-up is indicated from TaskTrack records."

    return {
        "attention_level": attention_level,
        "headline": headline,
        "recommendation": recommendation,
        "open_count": open_count,
        "overdue_count": len(overdue),
        "upcoming_event_count": len(upcoming),
        "next_event": next_event,
        "top_overdue": overdue[:3],
    }


def _project_action_queue(report: dict) -> list[dict]:
    """Meeting-facing next actions derived from visible report data."""
    actions: list[dict] = []
    overlay = report.get("operator_overlay") or {}
    report_note = (overlay.get("report_note") or "").strip()

    for item in (report.get("overdue_items") or [])[:3]:
        actions.append({
            "priority": "high",
            "title": f"Resolve overdue {item.get('label') or 'item'}: {item.get('title') or 'Untitled'}",
            "detail": f"Status: {item.get('status') or 'unknown'}",
            "source": item.get("label") or item.get("table") or "Linked record",
            "due": item.get("due") or "",
        })

    upcoming = list(report.get("upcoming_events") or [])
    if upcoming:
        event = upcoming[0]
        bits = [event.get("event_type") or "event", event.get("status") or ""]
        if event.get("location"):
            bits.append(event.get("location"))
        actions.append({
            "priority": "scheduled",
            "title": f"Prepare for {event.get('title') or 'upcoming project event'}",
            "detail": " / ".join([bit for bit in bits if bit]),
            "source": "Calendar",
            "due": event.get("start_at") or "",
        })

    linked = report.get("linked_records") or {}
    for table, source in (("project_work_tasks", "Project Tasks"), ("work_tasks", "CAD Dev"), ("training_tasks", "Training")):
        for row in linked.get(table, []):
            if not _is_open(table, row):
                continue
            if any(action.get("title", "").endswith(f": {_record_title(table, row)}") for action in actions):
                continue
            actions.append({
                "priority": "next",
                "title": f"Advance {source.lower()}: {_record_title(table, row)}",
                "detail": f"Status: {_status(row) or 'open'}",
                "source": source,
                "due": _record_when(table, row),
            })
            break
        if len(actions) >= 4:
            break

    if report_note:
        actions.append({
            "priority": "note",
            "title": "Management note",
            "detail": report_note,
            "source": "TaskTrack Overlay",
            "due": "",
        })

    if not actions:
        actions.append({
            "priority": "quiet",
            "title": "No immediate TaskTrack action",
            "detail": "No open linked work, overdue items, or upcoming project events are visible.",
            "source": "TaskTrack",
            "due": "",
        })
    return actions[:5]


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
                          is_admin: bool = False,
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
    workspace = project_workspace_payload(
        sess, proj, user_id=workspace_user_id, is_admin=is_admin
    )
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

    report = {
        "generated_at": now.isoformat(timespec="seconds"),
        "project": workspace["project"],
        "external": workspace["external"],
        "sites": workspace["sites"],
        "operator_overlay": workspace.get("operator_overlay") or {},
        "counts": workspace["counts"],
        "open_counts": open_counts,
        "overdue_items": overdue,
        "upcoming_events": upcoming[:12],
        "recent_activity": _recent_activity_for_project(
            sess, linked, is_admin=is_admin, limit=20,
        ),
        "linked_records": linked,
        "labels": REPORT_TABLES,
        "capabilities_visible": bool(is_admin),
    }
    report["management_brief"] = _project_management_brief(report)
    report["action_queue"] = _project_action_queue(report)
    return report


def meeting_packet_report(sess: Session, *, event_id: int,
                          user_id: int | None = None,
                          include_private: bool = False,
                          is_admin: bool = False,
                          now: datetime | None = None) -> dict | None:
    """Build a print-ready meeting packet from one visible calendar event."""
    event = sess.get(CalendarEvent, event_id)
    if event is None or not record_visible_to_user("calendar_events", event, user_id):
        return None

    now = now or datetime.now()
    project_report = None
    project_number = (event.project_number or "").strip()
    if event.project_id is not None or project_number:
        project_report = project_status_report(
            sess,
            project_id=event.project_id,
            project_number=project_number,
            user_id=user_id,
            include_private=include_private,
            is_admin=is_admin,
            now=now,
        )
        if project_report is not None:
            project_report["upcoming_events"] = [
                row for row in project_report.get("upcoming_events", [])
                if row.get("id") != event.id
            ]

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "event": _calendar_event_payload(event),
        "is_linked": project_report is not None,
        "project": project_report.get("project") if project_report else None,
        "project_report": project_report,
        "include_private": bool(include_private),
        "capabilities_visible": bool(is_admin),
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
    action_projects = []
    for report in reports:
        site_count += int(report.get("counts", {}).get("sites") or 0)
        overdue_count += len(report.get("overdue_items") or [])
        upcoming_count += len(report.get("upcoming_events") or [])
        for key in REPORT_TABLES:
            counts[key] += int(report.get("counts", {}).get(key) or 0)
            open_counts[key] += int(report.get("open_counts", {}).get(key) or 0)
        project = report.get("project") or {}
        brief = report.get("management_brief") or {}
        action = (report.get("action_queue") or [{}])[0]
        action_projects.append({
            "project_number": project.get("project_number") or "",
            "name": project.get("name") or "",
            "client": project.get("client") or "",
            "attention_level": brief.get("attention_level") or "quiet",
            "headline": brief.get("headline") or "",
            "primary_action": action.get("title") or "",
            "primary_action_detail": action.get("detail") or "",
            "overdue_count": len(report.get("overdue_items") or []),
            "open_count": _project_open_total(report),
            "next_due": action.get("due") or "",
        })
    attention_project_count = len([
        report for report in reports
        if (report.get("management_brief") or {}).get("attention_level") == "at_risk"
    ])
    rank = {"at_risk": 0, "scheduled": 1, "active": 2, "quiet": 3}
    action_projects.sort(key=lambda row: (
        rank.get(row.get("attention_level"), 9),
        -int(row.get("overdue_count") or 0),
        row.get("next_due") or "9999",
        row.get("project_number") or "",
    ))
    return {
        "project_count": len(reports),
        "site_count": site_count,
        "record_counts": counts,
        "open_counts": open_counts,
        "overdue_count": overdue_count,
        "upcoming_event_count": upcoming_count,
        "attention_project_count": attention_project_count,
        "action_projects": action_projects[:8],
    }


def portfolio_project_report(sess: Session, *, filters: dict | None = None,
                             user_id: int | None = None,
                             include_private: bool = False,
                             is_admin: bool = False,
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
            is_admin=is_admin,
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
        "capabilities_visible": bool(is_admin),
    }


__all__ = [
    "project_status_report",
    "meeting_packet_report",
    "portfolio_project_report",
    "REPORT_TABLES",
    "DEFAULT_PORTFOLIO_LIMIT",
    "MAX_PORTFOLIO_LIMIT",
]
