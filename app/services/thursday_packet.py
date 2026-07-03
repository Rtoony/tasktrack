"""Thursday Packet — the weekly management ritual document.

Management meets Thursday evenings; this packet is printed Thursday morning
and forwarded as an official department document. Three sections, in the
order the meeting reads them:

  completed  — Project Tasks + CAD Dev rows whose status is Complete and
               whose updated_at (the completion-time proxy — there is no
               completed_at column) falls inside the trailing window.
  in_flight  — active, non-complete rows that are either due within the
               next `due_days` or explicitly In Progress. Overdue rows are
               excluded here — they belong to risks, not commitments.
  risks      — overdue rows across both tables plus the portfolio at-risk
               summary rows (portfolio_project_report attention_level filter).

Management-facing by design: personnel/personal/calendar data is EXCLUDED,
and the portfolio report is built with is_admin=False / include_private=False
so capability narratives and private events can never leak into the packet.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ProjectWorkTask, WorkTask
from .project_reports import portfolio_project_report
from .tickets import done_statuses_for_table, is_overdue_value

# (table, model, person attr, due attr, display label) — the two management-
# visible work tables. Labels match project_reports.REPORT_TABLES vocabulary.
_PACKET_TABLES = (
    ("project_work_tasks", ProjectWorkTask, "engineer", "due_at", "Project Tasks"),
    ("work_tasks", WorkTask, "requested_by", "due_date", "CAD Dev"),
)

_AT_RISK_LIMIT = 12


def _parse_dt(raw) -> datetime | None:
    """Tolerant datetime parse for SQLite TIMESTAMP/text columns."""
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _row_payload(table: str, label: str, row, *, person_attr: str,
                 date_value: datetime | None, status: str) -> dict:
    return {
        "id": row.id,
        "table": table,
        "label": label,
        "title": row.title or "",
        "project_number": (row.project_number or "").strip(),
        "person": (getattr(row, person_attr, "") or "").strip(),
        "status": status,
        "date": date_value.isoformat(timespec="minutes") if date_value else "",
    }


def _week_of_line(now: datetime) -> tuple[str, str, str]:
    """Return ("Week of <Mon> – <Sun>", monday_iso, sunday_iso) for now's week."""
    monday = (now - timedelta(days=now.weekday())).date()
    sunday = monday + timedelta(days=6)
    label = (
        f"Week of {monday.strftime('%B')} {monday.day} – "
        f"{sunday.strftime('%B')} {sunday.day}, {sunday.year}"
    )
    return label, monday.isoformat(), sunday.isoformat()


def thursday_packet(sess: Session, *, window_days: int = 7, due_days: int = 7,
                    now: datetime | None = None) -> dict:
    """Build the Thursday management packet dict. Read-only."""
    now = now or datetime.now()
    window_start = now - timedelta(days=window_days)
    due_end_date = (now + timedelta(days=due_days)).date()

    completed: list[dict] = []
    in_flight: list[dict] = []
    overdue: list[dict] = []

    for table, model, person_attr, due_attr, label in _PACKET_TABLES:
        done_statuses = done_statuses_for_table(table)
        rows = sess.scalars(select(model).where(model.archived_at.is_(None))).all()
        for row in rows:
            status = (row.status or "").strip()
            if status in done_statuses:
                completed_at = _parse_dt(row.updated_at)
                if completed_at and window_start <= completed_at <= now:
                    completed.append(_row_payload(
                        table, label, row, person_attr=person_attr,
                        date_value=completed_at, status=status,
                    ))
                continue

            due_raw = getattr(row, due_attr, "") or ""
            due_dt = _parse_dt(due_raw)
            if is_overdue_value(due_raw):
                overdue.append(_row_payload(
                    table, label, row, person_attr=person_attr,
                    date_value=due_dt, status=status,
                ))
            elif (due_dt and now.date() <= due_dt.date() <= due_end_date) \
                    or status == "In Progress":
                in_flight.append(_row_payload(
                    table, label, row, person_attr=person_attr,
                    date_value=due_dt, status=status,
                ))

    completed.sort(key=lambda r: r["date"], reverse=True)
    # Rows without a due date (pure In Progress) sort after dated commitments.
    in_flight.sort(key=lambda r: (r["date"] == "", r["date"]))
    overdue.sort(key=lambda r: (r["date"] == "", r["date"]))

    # Portfolio at-risk summary — management-facing: never private, never admin.
    at_risk = portfolio_project_report(
        sess,
        filters={"attention_level": "at_risk", "limit": _AT_RISK_LIMIT},
        user_id=None,
        include_private=False,
        is_admin=False,
        now=now,
    )
    at_risk_summary = at_risk.get("summary") or {}
    at_risk_projects = list(at_risk_summary.get("action_projects") or [])
    at_risk_count = int(
        at_risk_summary.get("attention_project_count") or len(at_risk_projects)
    )

    week_of, week_start, week_end = _week_of_line(now)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {
            "days": window_days,
            "due_days": due_days,
            "start": window_start.isoformat(timespec="seconds"),
            "end": now.isoformat(timespec="seconds"),
            "due_until": due_end_date.isoformat(),
            "week_of": week_of,
            "week_start": week_start,
            "week_end": week_end,
        },
        "completed": completed,
        "in_flight": in_flight,
        "risks": {
            "overdue": overdue,
            "at_risk_projects": at_risk_projects,
            "at_risk_count": at_risk_count,
        },
        "counts": {
            "completed": len(completed),
            "in_flight": len(in_flight),
            "overdue": len(overdue),
            "at_risk_projects": len(at_risk_projects),
        },
    }


__all__ = ["thursday_packet"]
