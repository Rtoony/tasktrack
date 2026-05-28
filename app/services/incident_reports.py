"""Admin-only incident/capability report builders."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PersonnelIssue, to_dict

MAX_INCIDENT_LIMIT = 250
DEFAULT_INCIDENT_LIMIT = 100
_RESOLVED_STATUSES = {"resolved", "closed", "complete", "completed", "done"}
_HIGH_SEVERITIES = {"high", "critical"}


def _clean_text(value) -> str:
    return str(value or "").strip()


def _clean_limit(raw, default: int = DEFAULT_INCIDENT_LIMIT) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_INCIDENT_LIMIT))


def _clean_days(raw, default: int = 365) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 3650))


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _status(row: dict) -> str:
    return _clean_text(row.get("status"))


def _severity(row: dict) -> str:
    return _clean_text(row.get("severity"))


def _is_resolved(row: dict) -> bool:
    return _status(row).lower() in _RESOLVED_STATUSES


def _is_high(row: dict) -> bool:
    return _severity(row).lower() in _HIGH_SEVERITIES


def _is_follow_up_due(row: dict, today: date) -> bool:
    due = _parse_date(row.get("follow_up_date"))
    return bool(due and due <= today and not _is_resolved(row))


def _row_matches_text(row: dict, needle: str) -> bool:
    if not needle:
        return True
    haystack = " ".join(str(row.get(key) or "") for key in (
        "person_name", "observed_by", "cad_skill_area", "issue_description",
        "incident_context", "recommended_training", "resolution_notes",
        "project_number", "immediate_solution",
    ))
    return needle.lower() in haystack.lower()


def _incident_payload(row: PersonnelIssue, *, today: date) -> dict:
    payload = to_dict(row) or {}
    reported = _parse_dt(payload.get("reported_date"))
    payload["title"] = payload.get("issue_description") or f"Incident #{payload.get('id', '')}"
    payload["is_resolved"] = _is_resolved(payload)
    payload["is_high_severity"] = _is_high(payload)
    payload["follow_up_due"] = _is_follow_up_due(payload, today)
    payload["days_open"] = max(0, (today - reported.date()).days) if reported and not payload["is_resolved"] else 0
    payload["reported_at"] = payload.get("reported_date") or ""
    payload["project_report_url"] = (
        f"/reports/project?project_number={payload.get('project_number')}"
        if payload.get("project_number") else ""
    )
    return payload


def incident_report(sess: Session, *, filters: dict | None = None,
                    now: datetime | None = None) -> dict:
    """Build an admin-only incident report from personnel_issues."""
    filters = dict(filters or {})
    now = now or datetime.now()
    today = now.date()
    limit = _clean_limit(filters.get("limit"))
    q = _clean_text(filters.get("q"))
    severity = _clean_text(filters.get("severity"))
    status = _clean_text(filters.get("status"))
    project_number = _clean_text(filters.get("project_number"))
    person = _clean_text(filters.get("person"))
    open_only = bool(filters.get("open_only"))
    follow_up_due = bool(filters.get("follow_up_due"))
    days = _clean_days(filters.get("days"), default=365)
    since = now - timedelta(days=days)

    rows = sess.scalars(
        select(PersonnelIssue).order_by(PersonnelIssue.reported_date.desc(), PersonnelIssue.id.desc())
    ).all()

    incidents: list[dict] = []
    matched = 0
    for row in rows:
        payload = _incident_payload(row, today=today)
        reported = _parse_dt(payload.get("reported_date"))
        if reported and reported < since:
            continue
        if q and not _row_matches_text(payload, q):
            continue
        if severity and _severity(payload).lower() != severity.lower():
            continue
        if status and _status(payload).lower() != status.lower():
            continue
        if project_number and _clean_text(payload.get("project_number")) != project_number:
            continue
        if person and person.lower() not in _clean_text(payload.get("person_name")).lower():
            continue
        if open_only and payload["is_resolved"]:
            continue
        if follow_up_due and not payload["follow_up_due"]:
            continue
        matched += 1
        if len(incidents) < limit:
            incidents.append(payload)

    severity_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    project_counts: dict[str, int] = {}
    for row in incidents:
        severity_counts[_severity(row) or "Unspecified"] = severity_counts.get(_severity(row) or "Unspecified", 0) + 1
        status_counts[_status(row) or "Unspecified"] = status_counts.get(_status(row) or "Unspecified", 0) + 1
        project = _clean_text(row.get("project_number")) or "Unlinked"
        project_counts[project] = project_counts.get(project, 0) + 1

    summary = {
        "total": len(incidents),
        "matched_count": matched,
        "truncated": matched > len(incidents),
        "open_count": len([row for row in incidents if not row["is_resolved"]]),
        "resolved_count": len([row for row in incidents if row["is_resolved"]]),
        "high_severity_count": len([row for row in incidents if row["is_high_severity"]]),
        "follow_up_due_count": len([row for row in incidents if row["follow_up_due"]]),
        "estimated_time_loss_minutes": sum(int(row.get("estimated_time_loss_minutes") or 0) for row in incidents),
        "by_severity": severity_counts,
        "by_status": status_counts,
        "top_projects": sorted(
            [{"project_number": key, "count": value} for key, value in project_counts.items()],
            key=lambda item: (-item["count"], item["project_number"]),
        )[:8],
    }

    safe_filters = {
        "q": q,
        "severity": severity,
        "status": status,
        "project_number": project_number,
        "person": person,
        "open_only": open_only,
        "follow_up_due": follow_up_due,
        "days": days,
        "limit": limit,
    }
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "filters": safe_filters,
        "summary": summary,
        "incidents": incidents,
    }


def incident_detail_report(sess: Session, *, incident_id: int,
                           now: datetime | None = None) -> dict | None:
    """Build one printable admin-only incident packet."""
    row = sess.get(PersonnelIssue, incident_id)
    if row is None:
        return None
    now = now or datetime.now()
    incident = _incident_payload(row, today=now.date())
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "incident": incident,
        "project_report_url": incident.get("project_report_url") or "",
        "incident_report_url": f"/reports/incidents/{incident_id}",
        "incident_list_url": "/reports/incidents?open_only=1",
    }


INCIDENT_CSV_FIELDS = [
    "id", "reported_at", "person_name", "project_number", "severity", "status",
    "follow_up_date", "follow_up_due", "days_open", "estimated_time_loss_minutes",
    "cad_skill_area", "observed_by", "issue_description", "incident_context",
    "immediate_solution", "recommended_training", "resolution_notes",
    "project_report_url",
]


def incident_csv_rows(packet: dict) -> list[dict]:
    rows = []
    for item in packet.get("incidents", []):
        rows.append({field: item.get(field, "") for field in INCIDENT_CSV_FIELDS})
    return rows


__all__ = [
    "incident_report",
    "incident_detail_report",
    "incident_csv_rows",
    "INCIDENT_CSV_FIELDS",
]
