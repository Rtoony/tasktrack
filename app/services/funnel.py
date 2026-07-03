"""Funnel-drain signals (#72): what awaits a triage decision, what's rotting parked.

Shared by the bot-scoped morning digest (/api/v1/digest → the 06:05 Slack card)
and the session dashboard (/api/v1/dashboard → the command deck strip), so the
number RToony sees in Slack and the number on the landing page can never drift.

Personnel-destined captures (suggested_table == 'personnel_issues') are counted
but their titles are never exported — same sensitivity boundary as the digest's
TASK_TABLES excluding personnel_issues.
"""
from datetime import datetime, timedelta

from sqlalchemy import select

from ..models import InboxItem, WorkTask

SENSITIVE_SUGGESTED = "personnel_issues"


def funnel_counts(sess, stale_days: int = 14) -> dict:
    stale_cutoff = datetime.utcnow() - timedelta(days=stale_days)

    triage_rows = sess.scalars(
        select(InboxItem).where(InboxItem.status == "New")
        .order_by(InboxItem.created_at.asc())
    ).all()
    triage_redacted = sum(
        1 for r in triage_rows if (r.suggested_table or "") == SENSITIVE_SUGGESTED
    )
    triage_awaiting = [{
        "id": r.id, "title": r.title, "source": r.source or "",
        "created_at": str(r.created_at) if r.created_at else None,
    } for r in triage_rows if (r.suggested_table or "") != SENSITIVE_SUGGESTED][:10]

    parked = [
        r for r in sess.scalars(select(WorkTask)).all()
        if r.status == "Not Started"
        and getattr(r, "archived_at", None) is None
        and r.updated_at is not None and r.updated_at < stale_cutoff
    ]
    parked_sample = [{
        "id": r.id, "title": r.title, "category": r.category or "",
        "updated_at": str(r.updated_at),
    } for r in sorted(parked, key=lambda r: r.updated_at)[:10]]

    return {
        "stale_days": stale_days,
        "triage_total": len(triage_rows),
        "triage_redacted": triage_redacted,
        "triage_awaiting": triage_awaiting,
        "parked_total": len(parked),
        "parked_sample": parked_sample,
    }
