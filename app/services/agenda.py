"""Unified daily planning feed for TaskTrack.

The internal calendar owns dated events, but the operator's day also
depends on scheduled Project Tasks and due tracker work. This service
normalizes those records into one agenda contract for dashboard and
report surfaces.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CalendarEvent,
    PersonalItem,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
    to_dict,
)
from .tickets import done_statuses_for_table, record_visible_to_user


def _parse_dt(raw) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time.min)
        return datetime.fromisoformat(value.replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _iso_minutes(value: datetime) -> str:
    return value.isoformat(timespec="minutes")


def _duration_label(minutes) -> str:
    try:
        value = int(minutes or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    hours = value / 60
    return f"{int(hours) if value % 60 == 0 else hours:.1f}".rstrip("0").rstrip(".") + "h"


def _in_window(dt: datetime, *, start: datetime, end: datetime, include_overdue: bool) -> bool:
    if include_overdue and dt < start:
        return True
    return start <= dt <= end


def _event_item(row: CalendarEvent, when: datetime) -> dict:
    return {
        "kind": "calendar_event",
        "label": "Calendar",
        "table": "calendar_events",
        "id": row.id,
        "title": row.title or "(untitled event)",
        "status": row.status or "",
        "when": _iso_minutes(when),
        "date": when.date().isoformat(),
        "time_label": "All day" if row.all_day else when.strftime("%I:%M %p").lstrip("0"),
        "is_overdue": False,
        "event_type": row.event_type or "event",
        "project_number": row.project_number or "",
        "project_name": "",
        "priority": row.event_type or "",
        "detail": row.description or row.location or "",
        "duration_minutes": 0,
        "duration_label": "",
        "visibility": row.visibility or "internal",
        "url": f"/reports/meeting?event_id={row.id}",
    }


def _project_item(row: ProjectWorkTask, when: datetime, *, field: str,
                  start: datetime) -> dict:
    payload = to_dict(row) or {}
    return {
        "kind": "scheduled_project_task" if field == "scheduled_completion_at" else "due_task",
        "label": "Project Task",
        "table": "project_work_tasks",
        "id": row.id,
        "title": row.title or row.task_description or "(untitled project task)",
        "status": row.status or "",
        "when": _iso_minutes(when),
        "date": when.date().isoformat(),
        "time_label": when.strftime("%I:%M %p").lstrip("0"),
        "is_overdue": when < start,
        "event_type": "project_task",
        "project_number": row.project_number or "",
        "project_name": row.project_name or "",
        "priority": row.priority or "",
        "detail": (
            payload.get("progress_notes")
            or payload.get("scope_notes")
            or payload.get("task_description")
            or ""
        ),
        "duration_minutes": row.time_required_minutes or 0,
        "duration_label": _duration_label(row.time_required_minutes),
        "visibility": "internal",
        "url": f"/reports/project?project_number={row.project_number}" if row.project_number else "",
    }


def _due_item(table: str, label: str, row, when: datetime, *, field: str,
              start: datetime) -> dict:
    detail = (
        getattr(row, "description", "")
        or getattr(row, "training_goals", "")
        or getattr(row, "body", "")
        or getattr(row, "notes", "")
        or ""
    )
    project_number = getattr(row, "project_number", "") or ""
    tab_url = {
        "work_tasks": "/?tab=work",
        "training_tasks": "/?tab=training",
        "personal_items": "/?tab=personal_husband",
    }.get(table, "")
    return {
        "kind": "due_task",
        "label": label,
        "table": table,
        "id": row.id,
        "title": getattr(row, "title", "") or f"#{row.id}",
        "status": getattr(row, "status", "") or "",
        "when": _iso_minutes(when),
        "date": when.date().isoformat(),
        "time_label": when.strftime("%I:%M %p").lstrip("0") if "T" in str(getattr(row, field, "") or "") else "Due",
        "is_overdue": when < start,
        "event_type": "due",
        "project_number": project_number,
        "project_name": "",
        "priority": getattr(row, "priority", "") or "",
        "detail": detail,
        "duration_minutes": 0,
        "duration_label": "",
        "visibility": "internal",
        "url": f"/reports/project?project_number={project_number}" if project_number else tab_url,
    }


def today_agenda(
    sess: Session,
    *,
    days: int = 1,
    limit: int = 25,
    user_id: int | None = None,
    include_private: bool = False,
    include_overdue: bool = True,
    now: datetime | None = None,
) -> dict:
    """Return visible calendar and due/scheduled work for the planning window."""
    now = now or datetime.now()
    days = max(1, min(int(days or 1), 30))
    limit = max(1, min(int(limit or 25), 100))
    start = datetime.combine(now.date(), time.min)
    end = datetime.combine(now.date() + timedelta(days=days - 1), time.max)
    items: list[dict] = []

    for row in sess.scalars(select(CalendarEvent).order_by(CalendarEvent.start_at.asc())).all():
        if row.status in done_statuses_for_table("calendar_events"):
            continue
        if not record_visible_to_user("calendar_events", row, user_id):
            continue
        if row.visibility == "private" and not include_private:
            continue
        event_start = _parse_dt(row.start_at)
        if event_start is None:
            continue
        if row.all_day:
            event_start = datetime.combine(event_start.date(), time.min)
        if start <= event_start <= end:
            items.append(_event_item(row, event_start))

    seen_project_ids: set[int] = set()
    for row in sess.scalars(select(ProjectWorkTask).order_by(ProjectWorkTask.id.asc())).all():
        if row.status in done_statuses_for_table("project_work_tasks"):
            continue
        scheduled = _parse_dt(row.scheduled_completion_at)
        if scheduled is not None and _in_window(scheduled, start=start, end=end, include_overdue=include_overdue):
            items.append(_project_item(row, scheduled, field="scheduled_completion_at", start=start))
            seen_project_ids.add(row.id)
            continue
        due = _parse_dt(row.due_at)
        if due is not None and row.id not in seen_project_ids and _in_window(due, start=start, end=end, include_overdue=include_overdue):
            items.append(_project_item(row, due, field="due_at", start=start))

    due_sources = (
        ("work_tasks", "CAD Dev", WorkTask, "due_date"),
        ("training_tasks", "Training", TrainingTask, "due_date"),
        ("personal_items", "Internal Follow-up", PersonalItem, "due_date"),
    )
    for table, label, Model, field in due_sources:
        done = done_statuses_for_table(table)
        for row in sess.scalars(select(Model).order_by(Model.id.asc())).all():
            if getattr(row, "status", "") in done:
                continue
            when = _parse_dt(getattr(row, field, ""))
            if when is None or not _in_window(when, start=start, end=end, include_overdue=include_overdue):
                continue
            items.append(_due_item(table, label, row, when, field=field, start=start))

    items.sort(key=lambda item: (
        0 if item["is_overdue"] else 1,
        item["when"],
        item["label"],
        item["title"],
    ))
    visible = items[:limit]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    return {
        "available": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "window_start": start.isoformat(timespec="minutes"),
        "window_end": end.isoformat(timespec="minutes"),
        "days": days,
        "limit": limit,
        "count": len(visible),
        "matched_count": len(items),
        "truncated": len(items) > len(visible),
        "include_private": bool(include_private),
        "include_overdue": bool(include_overdue),
        "counts": counts,
        "items": visible,
    }
