"""Weekly snapshot service (Phase 6).

Pure-data aggregator: takes a SQLAlchemy session and a `since` datetime
and returns a structured dict the view layer can render any way it wants.
No Flask, no template concerns — the deliberate split lets us swap the
HTML format (which management will inevitably re-spec) without
touching the queries.

Heuristics (documented because reasonable people will second-guess them):
- "Created this week"   → row.created_at > since
- "Completed this week" → a logged status_change INTO a done status within the
                          window (from activity_log; the row must still exist and
                          still be done). EXACT — W2 replaced the old updated_at
                          heuristic, which any later edit bumped, so re-touching a
                          long-done item used to re-list it as completed this week.
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

WEEKLY_TRACKER_EXCLUDES = {"feedback_items"}

# Primary single-value "who owns this" field per tracker, used for the
# by-assignee breakdown. Tables absent from this map simply get no
# assignee breakdown (inbox/personal/calendar have no owner column).
# personnel_issues IS listed but its names are redaction-gated below.
ASSIGNEE_FIELD = {
    "work_tasks":         "requested_by",
    "project_work_tasks": "engineer",
    "training_tasks":     "requested_by",
    "personnel_issues":   "person_name",
}

# Age buckets (days since creation) for still-open items. Each entry is
# (low, high) in days, low-inclusive / high-inclusive; the final bucket
# is open-ended (high=None).
AGE_BUCKET_EDGES = [(0, 2), (3, 7), (8, 30), (31, None)]
AGE_BUCKET_LABELS = ["0-2d", "3-7d", "8-30d", "31d+"]

# Cap on distinct keys per breakdown dimension so a table with hundreds of
# distinct assignees can't bloat the JSON. Everything past the top-N (by
# count) rolls into a single "Other (N)" entry.
BREAKDOWN_KEY_LIMIT = 25

# Labels for special assignee/status cells.
REDACTED_ASSIGNEE = "Restricted"
UNASSIGNED_LABEL = "Unassigned"

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


def _created_dt(row) -> datetime | None:
    """Best-effort creation datetime for a row (created_at, else
    reported_date for personnel_issues). Returns None if unparseable."""
    for attr in ("created_at", "reported_date"):
        val = getattr(row, attr, None)
        if not val:
            continue
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val).replace(" ", "T"))
        except (ValueError, TypeError):
            continue
    return None


def _age_bucket_label(created: datetime | None, now: datetime) -> str:
    """Map an item's age (now - created, in days) onto an AGE_BUCKET label.
    Items with no parseable creation date land in the oldest bucket so they
    aren't silently dropped from the open-work age picture."""
    if created is None:
        return AGE_BUCKET_LABELS[-1]
    age_days = (now - created).days
    if age_days < 0:
        age_days = 0
    for (low, high), label in zip(AGE_BUCKET_EDGES, AGE_BUCKET_LABELS, strict=True):
        if age_days >= low and (high is None or age_days <= high):
            return label
    return AGE_BUCKET_LABELS[-1]


def _cap_counts(counts: dict[str, int]) -> dict[str, int]:
    """Sort a {key: count} map descending and roll everything past
    BREAKDOWN_KEY_LIMIT into a single 'Other (N)' entry so the JSON stays
    bounded regardless of cardinality."""
    if len(counts) <= BREAKDOWN_KEY_LIMIT:
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top = ordered[:BREAKDOWN_KEY_LIMIT]
    other_total = sum(c for _, c in ordered[BREAKDOWN_KEY_LIMIT:])
    out = dict(top)
    if other_total:
        out[f"Other ({len(ordered) - BREAKDOWN_KEY_LIMIT})"] = other_total
    return out


def _breakdowns_for_rows(rows: list, table: str, done: set, now: datetime,
                         *, include_sensitive: bool) -> dict:
    """Compute the by-status / by-assignee / age-bucket breakdowns.

    - by_status:   ALL visible rows grouped by their status value.
    - by_assignee: still-OPEN rows grouped by the table's assignee field
                   (only for tables in ASSIGNEE_FIELD). Sensitive
                   personnel names are redacted to 'Restricted' when the
                   caller isn't admin.
    - age_buckets: still-OPEN rows grouped by age-since-creation.
    """
    by_status: dict[str, int] = {}
    by_assignee: dict[str, int] = {}
    age_buckets: dict[str, int] = {label: 0 for label in AGE_BUCKET_LABELS}

    assignee_field = ASSIGNEE_FIELD.get(table)
    redact_assignee = table == "personnel_issues" and not include_sensitive

    for r in rows:
        status = getattr(r, "status", None) or "Unspecified"
        by_status[status] = by_status.get(status, 0) + 1

        is_open = status not in done if hasattr(r, "status") else True
        if not is_open:
            continue

        age_buckets[_age_bucket_label(_created_dt(r), now)] += 1

        if assignee_field:
            if redact_assignee:
                name = REDACTED_ASSIGNEE
            else:
                raw = getattr(r, assignee_field, None)
                name = str(raw).strip() if raw and str(raw).strip() else UNASSIGNED_LABEL
            by_assignee[name] = by_assignee.get(name, 0) + 1

    out = {
        "by_status": _cap_counts(by_status),
        "age_buckets": age_buckets,
    }
    if assignee_field:
        out["by_assignee"] = _cap_counts(by_assignee)
    return out


def _as_dt(value):
    """Coerce an activity_log timestamp (datetime or ISO string) to a datetime."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T"))
    except (ValueError, TypeError):
        return None


def _completed_in_window(sess: Session, table: str, since: datetime, done: set,
                         Model, *, user_id: int | None = None,
                         include_sensitive: bool = False) -> list[dict]:
    """W2: records that TRANSITIONED into a done status within (since, now], read
    from the polymorphic activity_log (action='status_change', new_value in `done`).

    Exact, unlike the old updated_at fallback: re-touching a long-completed item
    (e.g. adding a note) bumps updated_at and used to re-list it as "completed this
    week," inflating the green headline on the 3 core trackers that have no real
    completed_at column. One entry per record (latest in-window transition), kept
    only if the row still exists, is still done (not later reopened), is visible,
    and isn't archived."""
    if not done:
        return []
    # Inbox auto-file (inbox.py auto_filed) and promote (promoted) archive the item
    # — "Archived" is a done status for inbox_items — but log those action names, NOT
    # "status_change". Count them as done-transitions too, else in-window inbox
    # completions silently vanish from the headline (an opposite-direction undercount
    # the old updated_at path didn't have). The "row still done" guard below confirms
    # the archive stuck.
    DONE_TRANSITION_ACTIONS = ("auto_filed", "promoted")
    logs = sess.scalars(
        select(ActivityLog).where(
            ActivityLog.table_name == table,
            ActivityLog.action.in_(("status_change", *DONE_TRANSITION_ACTIONS)),
        )
    ).all()
    latest: dict[int, datetime] = {}
    for log in logs:
        ts = _as_dt(log.created_at)
        if ts is None or ts <= since:
            continue
        action = log.action or ""
        if action == "status_change":
            if (log.new_value or "") not in done:
                continue  # an edit to a non-done status, or a non-status field
        elif action not in DONE_TRANSITION_ACTIONS:
            continue
        prev = latest.get(log.record_id)
        if prev is None or ts > prev:
            latest[log.record_id] = ts
    items = []
    for rid, when in latest.items():
        row = sess.get(Model, rid)
        if row is None or getattr(row, "archived_at", None) is not None:
            continue
        if not record_visible_to_user(table, row, user_id):
            continue
        if hasattr(row, "status") and row.status not in done:
            continue  # reopened after the in-window completion — not "completed"
        items.append({
            "id": rid,
            "title": _title_for(row, table, include_sensitive=include_sensitive),
            "completed_at": when.isoformat(sep=" "),
        })
    items.sort(key=lambda d: d["completed_at"], reverse=True)
    return items


def _bucket_for_table(sess: Session, table: str, since: datetime,
                      user_id: int | None = None,
                      include_sensitive: bool = False,
                      breakdown: bool = False) -> dict:
    Model = TABLE_MODELS.get(table)
    if Model is None:
        return {}
    cfg = ALLOWED_TABLES[table]
    done = done_statuses_for_table(table)
    due_field = overdue_field_for_table(cfg)

    rows = [
        row for row in sess.scalars(select(Model)).all()
        if record_visible_to_user(table, row, user_id)
        and getattr(row, "archived_at", None) is None  # #38: archived out of the weekly report
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
        # (Completed is derived from activity_log after the loop — see W2 below.)

    # Most recent first.
    items_created.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    # W2: "completed this window" is derived from the activity_log status-change
    # history (an exact record of when each item became done), NOT from updated_at,
    # which any later edit bumps — re-touching a months-old done item used to
    # re-list it as "completed this week" and inflate the headline.
    items_completed = _completed_in_window(
        sess, table, since, done, Model,
        user_id=user_id, include_sensitive=include_sensitive,
    )

    bucket = {
        "table": table,
        "label": BUCKET_LABELS.get(table, table),
        "created": len(items_created),
        "completed": len(items_completed),
        "active_now": active,
        "overdue_now": overdue_now,
        "items_created": items_created[:ITEM_LIMIT],
        "items_completed": items_completed[:ITEM_LIMIT],
    }
    if breakdown:
        bucket["breakdown"] = _breakdowns_for_rows(
            rows, table, done, datetime.utcnow(),
            include_sensitive=include_sensitive,
        )
    return bucket


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
    rows = sess.scalars(select(PersonnelIssue).where(PersonnelIssue.archived_at.is_(None))).all()  # #38
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
                    user_id: int | None = None,
                    breakdown: bool = False) -> dict:
    """Compute the weekly digest.

    If `since` is None, derives it from `days` (default 7) against now-UTC.
    `include_admin=True` turns on the skill-score-changes bucket.
    `breakdown=True` adds a per-bucket `breakdown` block (by_status /
    by_assignee / age_buckets) for status-rollup reporting. It is opt-in so
    the default payload shape (and its callers) stay unchanged.

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
        if table in WEEKLY_TRACKER_EXCLUDES:
            continue
        b = _bucket_for_table(
            sess, table, since_naive, user_id=user_id,
            include_sensitive=include_admin, breakdown=breakdown,
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


__all__ = [
    "weekly_snapshot", "BUCKET_LABELS", "ITEM_LIMIT",
    "AGE_BUCKET_LABELS", "ASSIGNEE_FIELD", "BREAKDOWN_KEY_LIMIT",
    "REDACTED_ASSIGNEE", "UNASSIGNED_LABEL",
]
