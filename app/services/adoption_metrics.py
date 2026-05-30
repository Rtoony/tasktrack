"""Read-only adoption metrics for the TaskTrack daily-use trial."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ActivityLog,
    CalendarEvent,
    Comment,
    InboxItem,
    PersonalItem,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
)

DONE_STATUSES = {
    "Done",
    "Complete",
    "Completed",
    "Closed",
    "Archived",
    "Resolved",
    "Cancelled",
}

TRIAL_TARGETS = {
    "activity_days": 8,
    "created_or_followup_records": 10,
    "project_linked_records": 5,
    "comments": 1,
    "calendar_future_events": 1,
}


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _is_since(raw, since: datetime) -> bool:
    value = _parse_dt(raw)
    return value is not None and value >= since


def _is_future(raw, now: datetime) -> bool:
    value = _parse_dt(raw)
    return value is not None and value >= now


def _has_project_ref(row) -> bool:
    project_number = str(getattr(row, "project_number", "") or "").strip()
    project_id = getattr(row, "project_id", None)
    return bool(project_number or project_id)


def _open_status(status: str | None) -> bool:
    return (status or "").strip() not in DONE_STATUSES


def _row_created_at(row):
    return getattr(row, "created_at", None) or getattr(row, "reported_date", None)


def _tracker_rows(sess: Session) -> dict[str, list]:
    return {
        "work_tasks": list(sess.scalars(select(WorkTask)).all()),
        "project_work_tasks": list(sess.scalars(select(ProjectWorkTask)).all()),
        "training_tasks": list(sess.scalars(select(TrainingTask)).all()),
        "personal_items": list(sess.scalars(select(PersonalItem)).all()),
    }


def adoption_metrics(sess: Session, *, days: int = 14, now: datetime | None = None) -> dict:
    """Return trial evidence without mutating TaskTrack state."""
    days = max(1, min(int(days or 14), 90))
    now = now or datetime.now()
    since = now - timedelta(days=days)

    activity_rows = list(sess.scalars(select(ActivityLog)).all())
    activity_recent = [row for row in activity_rows if _is_since(row.created_at, since)]
    activity_days = sorted({
        parsed.date().isoformat()
        for row in activity_recent
        if (parsed := _parse_dt(row.created_at)) is not None
    })

    comments = list(sess.scalars(select(Comment)).all())
    recent_comments = [row for row in comments if _is_since(row.created_at, since)]

    inbox_rows = list(sess.scalars(select(InboxItem)).all())
    recent_inbox = [row for row in inbox_rows if _is_since(row.created_at, since)]
    open_inbox = [row for row in inbox_rows if _open_status(row.status)]
    promoted_inbox = [
        row for row in inbox_rows
        if str(row.promoted_to_table or "").strip() or row.promoted_to_id is not None
    ]

    calendars = list(sess.scalars(select(CalendarEvent)).all())
    future_calendar = [row for row in calendars if _is_future(row.start_at, now)]
    recent_calendar = [row for row in calendars if _is_since(row.created_at, since)]

    trackers = _tracker_rows(sess)
    created_or_followup = len(recent_inbox) + len(recent_comments)
    project_linked = 0
    active_by_table = {}
    created_by_table = {}
    for table, rows in trackers.items():
        active_by_table[table] = sum(
            1 for row in rows if _open_status(getattr(row, "status", ""))
        )
        created_by_table[table] = sum(
            1 for row in rows if _is_since(_row_created_at(row), since)
        )
        created_or_followup += created_by_table[table]
        project_linked += sum(
            1
            for row in rows
            if _has_project_ref(row) and _is_since(_row_created_at(row), since)
        )
    project_linked += sum(
        1
        for row in calendars
        if _has_project_ref(row) and _is_since(row.created_at, since)
    )

    target_values = {
        "activity_days": len(activity_days),
        "created_or_followup_records": created_or_followup,
        "project_linked_records": project_linked,
        "comments": len(recent_comments),
        "calendar_future_events": len(future_calendar),
    }
    targets = {
        key: {
            "actual": actual,
            "target": TRIAL_TARGETS[key],
            "met": actual >= TRIAL_TARGETS[key],
        }
        for key, actual in target_values.items()
    }

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {
            "days": days,
            "since": since.isoformat(timespec="seconds"),
        },
        "summary": {
            "active_days": len(activity_days),
            "created_or_followup_records": created_or_followup,
            "project_linked_records": project_linked,
            "future_calendar_events": len(future_calendar),
            "open_inbox": len(open_inbox),
            "recent_comments": len(recent_comments),
            "targets_met": all(item["met"] for item in targets.values()),
        },
        "activity": {
            "total": len(activity_rows),
            "recent": len(activity_recent),
            "active_days": activity_days,
            "by_table": dict(Counter(row.table_name for row in activity_recent)),
            "by_action": dict(Counter(row.action for row in activity_recent)),
        },
        "inbox": {
            "total": len(inbox_rows),
            "recent_created": len(recent_inbox),
            "open": len(open_inbox),
            "promoted_total": len(promoted_inbox),
        },
        "trackers": {
            "active_by_table": active_by_table,
            "created_by_table": created_by_table,
        },
        "calendar": {
            "total": len(calendars),
            "recent_created": len(recent_calendar),
            "future": len(future_calendar),
        },
        "comments": {
            "total": len(comments),
            "recent": len(recent_comments),
        },
        "targets": targets,
    }


__all__ = ["TRIAL_TARGETS", "adoption_metrics"]
