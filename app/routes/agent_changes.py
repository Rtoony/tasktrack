"""Bot-scoped change-feed (the outbound half of the Hermes two-way loop).

Hermes already READS the digest/agenda and can WRITE task/feedback status. The
missing piece (WORK_PLAN P2-7) is letting Hermes *learn what changed* between
briefings without re-scanning every tracker: closures, priority changes, new
comments, status moves. This is a cursor-driven tail of the activity log so a
headless poller never double-processes a change.

    GET /api/v1/changes?after_id=<id>&since_hours=<n>&limit=<n>&tables=a,b

`after_id` is the monotonic cursor — pass back the `next_cursor` from the prior
call and you get only rows newer than that, in ascending id order. On the first
call (no cursor) it falls back to a `since_hours` time window (default 24h, max
168h) so a fresh poller gets a bounded recent slice instead of all history.

Token-authenticated with the ``bot`` scope. READ-ONLY — it only reads the
activity log + resolves a human title per touched record. Limited to the same
tracker set the digest exposes (the three task trackers + feedback_items),
deliberately excluding personnel_issues / calendar / personal items.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from ..db import get_session
from ..models import ActivityLog, to_dict
from ..services.tickets import TABLE_MODELS
from ..tokens import check_scoped_token

bp = Blueprint("agent_changes", __name__)

# Trackers whose movement is relevant to a Hermes briefing. Mirrors the
# digest's task set plus feedback (the co-dev close-the-loop surface).
# Deliberately excludes personnel_issues (sensitive), calendar/personal.
CHANGE_TABLES = ("work_tasks", "project_work_tasks", "training_tasks",
                 "feedback_items")

_DEFAULT_SINCE_HOURS = 24
_MAX_SINCE_HOURS = 168
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


def _skip_limit_for_tests() -> bool:
    return bool(current_app.config.get("TESTING"))


def _clamp(raw, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(value, hi))


def _record_title(table: str, row_dict: dict) -> str:
    return (
        row_dict.get("title")
        or row_dict.get("project_name")
        or f"#{row_dict.get('id', '?')}"
    )


def _requested_tables() -> tuple[str, ...]:
    """Optional ?tables=a,b filter, intersected with the allow-list."""
    raw = (request.args.get("tables") or "").strip()
    if not raw:
        return CHANGE_TABLES
    wanted = {t.strip() for t in raw.split(",") if t.strip()}
    selected = tuple(t for t in CHANGE_TABLES if t in wanted)
    return selected or CHANGE_TABLES


@bp.route("/api/v1/changes", methods=["GET"])
def changes():
    err = check_scoped_token("bot")
    if err:
        return err

    tables = _requested_tables()
    limit = _clamp(request.args.get("limit"), _DEFAULT_LIMIT, 1, _MAX_LIMIT)
    after_id_raw = request.args.get("after_id")

    sess = get_session()
    stmt = select(ActivityLog).where(ActivityLog.table_name.in_(tables))

    using_cursor = False
    since_hours = None
    if after_id_raw is not None and str(after_id_raw).strip() != "":
        try:
            after_id = int(after_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "after_id must be an integer"}), 400
        stmt = stmt.where(ActivityLog.id > after_id)
        using_cursor = True
    else:
        # No cursor: bound to a recent time window so a fresh poller doesn't
        # pull all of history on its first call.
        since_hours = _clamp(request.args.get("since_hours"),
                             _DEFAULT_SINCE_HOURS, 1, _MAX_SINCE_HOURS)
        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        stmt = stmt.where(ActivityLog.created_at >= cutoff)

    # Ascending id so the caller can advance a monotonic cursor. Pull one extra
    # row to detect has_more without a second count query.
    stmt = stmt.order_by(ActivityLog.id.asc()).limit(limit + 1)
    rows = sess.scalars(stmt).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    # Resolve a human title per touched record, caching within the request so a
    # busy record (many activity rows) is only looked up once.
    title_cache: dict[tuple[str, int], str] = {}

    def _title(table: str, record_id: int) -> str:
        key = (table, record_id)
        if key not in title_cache:
            Model = TABLE_MODELS.get(table)
            target = sess.get(Model, record_id) if Model else None
            title_cache[key] = (
                _record_title(table, to_dict(target) or {}) if target else ""
            )
        return title_cache[key]

    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "table": r.table_name,
            "record_id": r.record_id,
            "record_title": _title(r.table_name, r.record_id),
            "action": r.action,
            "field": r.field_name or "",
            "old_value": r.old_value or "",
            "new_value": r.new_value or "",
            "user_name": r.user_name or "",
            "at": r.created_at.isoformat() if r.created_at else None,
            "record_url": f"/?tab={r.table_name}&record={r.record_id}",
        })

    next_cursor = out[-1]["id"] if out else (
        int(after_id_raw) if using_cursor else None
    )

    return jsonify({
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S") + "Z",
        "cursor": {
            "after_id": int(after_id_raw) if using_cursor else None,
            "since_hours": since_hours,
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
        "tables": list(tables),
        "count": len(out),
        "changes": out,
    })
