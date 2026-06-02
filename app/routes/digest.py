"""Bot-scoped morning-digest endpoint.

A read-only snapshot of the work that needs attention — overdue tasks,
tasks due soon, and recent project movement — across the three task
trackers (work_tasks, project_work_tasks, training_tasks). Token-
authenticated with the ``bot`` scope so headless callers (the Hermes
morning briefing) never need a session cookie or direct DB access.

    GET /api/v1/digest?due_days=7&activity_hours=24

Returns JSON only; rendering is the caller's job. The overdue / done-
status semantics are reused from ``services.tickets`` so this endpoint
and the dashboard never drift.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from .. import limiter
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import ActivityLog, to_dict
from ..services.tickets import (
    TABLE_MODELS,
    done_statuses_for_table,
    is_overdue_value,
    overdue_field_for_table,
)
from ..tokens import check_scoped_token

bp = Blueprint("digest", __name__)

# The trackers that represent actionable work for the morning briefing.
# Deliberately excludes personnel_issues (sensitive), calendar_events,
# and inbox/personal/feedback — those aren't "tasks due" in this sense.
TASK_TABLES = ("work_tasks", "project_work_tasks", "training_tasks")

_DEFAULT_DUE_DAYS = 7
_DEFAULT_ACTIVITY_HOURS = 24
_RECENT_SCAN_LIMIT = 100


def _skip_limit_for_tests() -> bool:
    return bool(current_app.config.get("TESTING"))


def _clamp(raw, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(value, hi))


def _due_date(raw):
    """Parse a due value (date or datetime ISO string) to a date, or None."""
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _record_title(table: str, row_dict: dict) -> str:
    return (
        row_dict.get("title")
        or row_dict.get("project_name")
        or f"#{row_dict.get('id', '?')}"
    )


def _task_item(table: str, due_field: str, row) -> dict:
    d = to_dict(row) or {}
    return {
        "table": table,
        "id": d.get("id"),
        "title": _record_title(table, d),
        "status": d.get("status", ""),
        "priority": d.get("priority", ""),
        "due": d.get(due_field, "") if due_field else "",
        "project_number": d.get("project_number", "") or "",
        "project_name": d.get("project_name", "") or "",
        "engineer": d.get("engineer", "") or "",
    }


@bp.route("/api/v1/digest", methods=["GET"])
@limiter.limit("60 per minute; 600 per hour", exempt_when=_skip_limit_for_tests)
def digest():
    err = check_scoped_token("bot")
    if err:
        return err

    due_days = _clamp(request.args.get("due_days"), _DEFAULT_DUE_DAYS, 1, 90)
    activity_hours = _clamp(
        request.args.get("activity_hours"), _DEFAULT_ACTIVITY_HOURS, 1, 168
    )
    today = date.today()
    horizon = today + timedelta(days=due_days)

    sess = get_session()
    overdue: list[dict] = []
    due_today: list[dict] = []
    due_soon: list[dict] = []
    by_table: dict[str, dict] = {}

    for table in TASK_TABLES:
        cfg = ALLOWED_TABLES[table]
        Model = TABLE_MODELS[table]
        due_field = overdue_field_for_table(cfg)
        done = done_statuses_for_table(table)
        counts = {"active": 0, "overdue": 0, "due_soon": 0}

        for row in sess.scalars(select(Model)).all():
            if getattr(row, "status", None) in done:
                continue
            counts["active"] += 1
            if due_field is None:
                continue
            raw_due = getattr(row, due_field, "") or ""
            if is_overdue_value(raw_due):
                overdue.append(_task_item(table, due_field, row))
                counts["overdue"] += 1
                continue
            parsed = _due_date(raw_due)
            if parsed is None:
                continue
            if parsed == today:
                due_today.append(_task_item(table, due_field, row))
            if today <= parsed <= horizon:
                due_soon.append(_task_item(table, due_field, row))
                counts["due_soon"] += 1

        by_table[table] = counts

    # Soonest first; overdue shows the most-overdue (oldest due) first.
    overdue.sort(key=lambda i: _due_date(i["due"]) or date.max)
    due_soon.sort(key=lambda i: _due_date(i["due"]) or date.max)
    due_today.sort(key=lambda i: (i.get("priority", ""), i.get("title", "")))

    # Recent movement: pull a bounded recent slice, then filter to the
    # window in Python (created_at is a real datetime on read, so there's
    # no string-compare fragility) and resolve a human title per row.
    cutoff = datetime.utcnow() - timedelta(hours=activity_hours)
    recent_rows = sess.scalars(
        select(ActivityLog)
        .where(ActivityLog.table_name.in_(TASK_TABLES))
        .order_by(ActivityLog.created_at.desc())
        .limit(_RECENT_SCAN_LIMIT)
    ).all()
    recent_activity: list[dict] = []
    for r in recent_rows:
        if r.created_at is None or r.created_at < cutoff:
            continue
        Model = TABLE_MODELS.get(r.table_name)
        title = ""
        if Model is not None:
            target = sess.get(Model, r.record_id)
            if target is not None:
                title = _record_title(r.table_name, to_dict(target) or {})
        recent_activity.append({
            "table": r.table_name,
            "record_id": r.record_id,
            "record_title": title,
            "action": r.action,
            "field": r.field_name or "",
            "new_value": r.new_value or "",
            "user_name": r.user_name or "",
            "at": r.created_at.isoformat(),
        })

    return jsonify({
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "window": {"due_days": due_days, "activity_hours": activity_hours},
        "counts": {
            "overdue": len(overdue),
            "due_today": len(due_today),
            "due_soon": len(due_soon),
            "active": sum(c["active"] for c in by_table.values()),
            "by_table": by_table,
        },
        "overdue": overdue,
        "due_today": due_today,
        "due_soon": due_soon,
        "recent_activity": recent_activity,
    })
