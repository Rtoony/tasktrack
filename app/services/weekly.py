"""Weekly snapshot service (Phase 6).

Pure-data aggregator: takes a SQLAlchemy session and a `since` datetime
and returns a structured dict the view layer can render any way it wants.
No Flask, no template concerns — the deliberate split lets us swap the
HTML format (which management will inevitably re-spec) without
touching the queries.

Heuristics (documented because reasonable people will second-guess them):
- "Created this week"   → row.created_at > since
- "Completed this week" → row.status in done_statuses(table)
                          AND row.updated_at > since
                          (or row.completed_at if the table has it).
                          Approximate — any UPDATE bumps updated_at;
                          status-transition history lives in activity_log
                          if you ever need exact dates.
- "Active now"          → row.status NOT in done_statuses(table)
- "Overdue now"         → active AND past the table's due field.

Admin-only buckets (skill_score_changes) are gated by the caller; this
module just computes them when asked.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ALLOWED_TABLES
from ..models import ActivityLog
from .tickets import (
    TABLE_MODELS,
    done_statuses_for_table,
    is_overdue_value,
    overdue_field_for_table,
    record_visible_to_user,
)

# Display labels for the weekly buckets — friendlier than the raw table name.
BUCKET_LABELS = {
    "work_tasks":         "CAD Dev",
    "project_work_tasks": "Project Tasks",
    "training_tasks":     "Training",
    "personnel_issues":   "Incidents / Capabilities",
    "inbox_items":        "Triage / Inbox",
    "personal_items":     "Internal",
    "calendar_events":    "Calendar",
}

# Limit the size of `items_*` lists so the JSON doesn't bloat.
ITEM_LIMIT = 50


def _row_was_updated_since(row, since: datetime) -> bool:
    """True if the row's `updated_at` (or `completed_at` if present) is
    newer than `since`. Handles strings and datetimes."""
    for attr in ("completed_at", "updated_at"):
        val = getattr(row, attr, None)
        if not val:
            continue
        if isinstance(val, datetime):
            return val > since
        try:
            return datetime.fromisoformat(str(val).replace(" ", "T")) > since
        except (ValueError, TypeError):
            continue
    return False


def _row_created_since(row, since: datetime) -> bool:
    """True if the row's creation timestamp is newer than `since`.
    personnel_issues uses `reported_date` instead of `created_at` — try
    both, return on the first hit."""
    for attr in ("created_at", "reported_date"):
        val = getattr(row, attr, None)
        if not val:
            continue
        if isinstance(val, datetime):
            return val > since
        try:
            return datetime.fromisoformat(str(val).replace(" ", "T")) > since
        except (ValueError, TypeError):
            continue
    return False


def _title_for(row, table: str, *, include_sensitive: bool = False) -> str:
    """Best-guess display title with capability narratives gated."""
    if hasattr(row, "title") and row.title:
        return str(row.title)
    if table == "personnel_issues":
        if not include_sensitive:
            return "Capability note (restricted)"
        pn = (row.person_name or "(no person)") if hasattr(row, "person_name") else "?"
        desc = (row.issue_description or "")[:60]
        return f"{pn} — {desc}" if desc else pn
    return f"#{getattr(row, 'id', '?')}"


def _bucket_for_table(sess: Session, table: str, since: datetime,
                      user_id: int | None = None,
                      include_sensitive: bool = False) -> dict:
    Model = TABLE_MODELS.get(table)
    if Model is None:
        return {}
    cfg = ALLOWED_TABLES[table]
    done = done_statuses_for_table(table)
    due_field = overdue_field_for_table(cfg)

    rows = [
        row for row in sess.scalars(select(Model)).all()
        if record_visible_to_user(table, row, user_id)
    ]
    items_created = []
    items_completed = []
    active = 0
    overdue_now = 0

    for r in rows:
        in_done = r.status in done if hasattr(r, "status") else False
        if not in_done:
            active += 1
            if due_field and is_overdue_value(getattr(r, due_field, None)):
                overdue_now += 1
        # Created bucket — pull the appropriate timestamp attr
        # (created_at on most tables; reported_date on personnel_issues).
        if _row_created_since(r, since):
            ts = (getattr(r, "created_at", None)
                  or getattr(r, "reported_date", None))
            items_created.append({
                "id": r.id,
                "title": _title_for(r, table, include_sensitive=include_sensitive),
                "status": getattr(r, "status", None),
                "created_at": (ts.isoformat(sep=" ")
                               if isinstance(ts, datetime) else str(ts or "")),
            })
        # Completed bucket
        if in_done and _row_was_updated_since(r, since):
            ts = (getattr(r, "completed_at", None)
                  or getattr(r, "updated_at", None))
            items_completed.append({
                "id": r.id,
                "title": _title_for(r, table, include_sensitive=include_sensitive),
                "completed_at": (ts.isoformat(sep=" ")
                                 if isinstance(ts, datetime) else str(ts or "")),
            })

    # Most recent first within each bucket.
    items_created.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    items_completed.sort(key=lambda d: d.get("completed_at") or "", reverse=True)

    return {
        "table": table,
        "label": BUCKET_LABELS.get(table, table),
        "created": len(items_created),
        "completed": len(items_completed),
        "active_now": active,
        "overdue_now": overdue_now,
        "items_created": items_created[:ITEM_LIMIT],
        "items_completed": items_completed[:ITEM_LIMIT],
    }


def _skill_score_changes(sess: Session, since: datetime) -> list[dict]:
    """Pulled from the polymorphic activity_log keyed by
    `employee_skill_scores`. Joins back to Employee + SkillCategory for
    display. Admin-only on the caller side."""
    rows = sess.scalars(
        select(ActivityLog).where(
            ActivityLog.table_name == "employee_skill_scores",
        )
    ).all()
    out = []
    for row in rows:
        ts = row.created_at
        if isinstance(ts, datetime):
            if ts <= since:
                continue
        else:
            try:
                if datetime.fromisoformat(
                    str(ts).replace(" ", "T")
                ) <= since:
                    continue
            except (ValueError, TypeError):
                continue
        # record_id on the activity_log is the EmployeeSkillScore.id,
        # which doesn't directly tell us employee/category — but the
        # field_name + old/new captures the score change. Score row
        # lookup adds a query; keep it simple and just show raw change.
        out.append({
            "score_row_id": row.record_id,
            "action": row.action,
            "field": row.field_name,
            "old": row.old_value,
            "new": row.new_value,
            "when": (ts.isoformat(sep=" ")
                     if isinstance(ts, datetime) else str(ts)),
        })
    out.sort(key=lambda d: d.get("when") or "", reverse=True)
    return out[:ITEM_LIMIT]


def _recent_incidents(sess: Session, since: datetime, *,
                      include_sensitive: bool = False) -> list[dict]:
    """personnel_issues rows whose created_at > since, narrative-gated."""
    from ..models import PersonnelIssue
    rows = sess.scalars(select(PersonnelIssue)).all()
    out = []
    for r in rows:
        if not _row_created_since(r, since):
            continue
        ts = getattr(r, "reported_date", None)
        item = {
            "id": r.id,
            "severity": r.severity,
            "status": r.status,
            "created_at": (ts.isoformat(sep=" ")
                           if isinstance(ts, datetime)
                           else str(ts or "")),
        }
        if include_sensitive:
            item.update({
                "person_name": r.person_name or "(no person)",
                "issue_description": (r.issue_description or "")[:140],
                "redacted": False,
            })
        else:
            item.update({
                "person_name": "Restricted",
                "issue_description": "Capability narrative restricted",
                "redacted": True,
            })
        out.append(item)
    out.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return out[:ITEM_LIMIT]


def weekly_snapshot(sess: Session, since: datetime | None = None,
                    days: int = 7, include_admin: bool = False,
                    user_id: int | None = None) -> dict:
    """Compute the weekly digest.

    If `since` is None, derives it from `days` (default 7) against now-UTC.
    `include_admin=True` turns on the skill-score-changes bucket.

    SQLite stores naive datetimes (no tzinfo). The comparators below all
    receive a naive `since`; the JSON shape carries an ISO string that
    callers can interpret as UTC.
    """
    until_aware = datetime.now(tz=UTC)
    if since is None:
        since_aware = until_aware - timedelta(days=days)
    else:
        since_aware = since if since.tzinfo else since.replace(tzinfo=UTC)
    since_naive = since_aware.replace(tzinfo=None)

    buckets: dict[str, dict] = {}
    totals = {"created": 0, "completed": 0, "active_now": 0, "overdue_now": 0}
    for table in ALLOWED_TABLES:
        b = _bucket_for_table(
            sess, table, since_naive, user_id=user_id,
            include_sensitive=include_admin,
        )
        if not b:
            continue
        buckets[table] = b
        totals["created"] += b["created"]
        totals["completed"] += b["completed"]
        totals["active_now"] += b["active_now"]
        totals["overdue_now"] += b["overdue_now"]

    snapshot = {
        "since": since_aware.isoformat(timespec="seconds"),
        "until": until_aware.isoformat(timespec="seconds"),
        "days": days,
        "totals": totals,
        "buckets": buckets,
        "incidents_recent": _recent_incidents(
            sess, since_naive, include_sensitive=include_admin,
        ),
    }
    if include_admin:
        snapshot["skill_score_changes"] = _skill_score_changes(sess, since_naive)
    return snapshot


__all__ = ["weekly_snapshot", "BUCKET_LABELS", "ITEM_LIMIT"]
