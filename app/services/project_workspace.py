"""Project workspace aggregation shared by registry and reports."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ActivityLog,
    CalendarEvent,
    PersonnelIssue,
    Project,
    ProjectOverlay,
    ProjectSite,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
    to_dict,
)
from .tickets import record_visible_to_user

WORKSPACE_TABLE_LABELS = {
    "project_work_tasks": "Project Tasks",
    "calendar_events": "Calendar",
    "work_tasks": "CAD Dev",
    "training_tasks": "Training",
    "personnel_issues": "Capabilities",
}


def _redact_personnel_issue(row: dict) -> dict:
    """Return count/status-safe capability data for non-admin report surfaces."""
    return {
        "id": row.get("id"),
        "title": "Capability note (restricted)",
        "status": row.get("status") or "",
        "severity": row.get("severity") or "",
        "reported_date": row.get("reported_date") or "",
        "follow_up_date": row.get("follow_up_date") or "",
        "estimated_time_loss_minutes": row.get("estimated_time_loss_minutes") or 0,
        "project_id": row.get("project_id"),
        "project_number": row.get("project_number") or "",
        "updated_at": row.get("updated_at") or "",
        "redacted": True,
    }


def linked_rows(sess: Session, model, project_id: int, project_number: str,
                *, user_id: int | None = None, is_admin: bool = False,
                limit: int = 50) -> list[dict]:
    """Rows linked by FK or human project_number, privacy-filtered."""
    stmt = select(model).where(
        (model.project_id == project_id) | (model.project_number == project_number)
    )
    if model is CalendarEvent:
        if user_id is None:
            stmt = stmt.where(model.visibility != "private")
        else:
            stmt = stmt.where(
                (model.visibility != "private") | (model.created_by_user_id == user_id)
            )
    rows = sess.scalars(stmt.order_by(model.id.desc()).limit(limit)).all()
    serialized = [
        to_dict(row) for row in rows
        if record_visible_to_user(model.__tablename__, row, user_id)
    ]
    if model is PersonnelIssue and not is_admin:
        return [_redact_personnel_issue(row) for row in serialized]
    return serialized


def _record_title(table: str, row: dict) -> str:
    if table == "personnel_issues":
        return (
            row.get("title")
            or row.get("issue_description")
            or row.get("person_name")
            or f"#{row.get('id', '?')}"
        )
    return row.get("title") or row.get("project_name") or row.get("name") or f"#{row.get('id', '?')}"


def recent_activity_for_linked_records(sess: Session, linked_records: dict[str, list[dict]],
                                       *, is_admin: bool = False,
                                       limit: int = 20) -> list[dict]:
    """Recent activity for visible project-linked rows.

    Capability/personnel audit rows are narrative-heavy, so non-admin
    workspace/report surfaces omit them rather than trying to redact each
    old/new value.
    """
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for table, rows in linked_records.items():
        if table == "personnel_issues" and not is_admin:
            continue
        for row in rows:
            raw_id = row.get("id")
            try:
                record_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            key = (table, record_id)
            if key in seen:
                continue
            seen.add(key)
            activity_rows = sess.scalars(
                select(ActivityLog)
                .where(
                    ActivityLog.table_name == table,
                    ActivityLog.record_id == record_id,
                )
                .order_by(ActivityLog.created_at.desc())
                .limit(5)
            ).all()
            record_title = _record_title(table, row)
            label = WORKSPACE_TABLE_LABELS.get(table, table)
            for activity in activity_rows:
                payload = to_dict(activity) or {}
                payload.update({
                    "label": label,
                    "record_title": record_title,
                })
                out.append(payload)
    out.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return out[:limit]


def _empty_project_overlay(proj: Project) -> dict:
    return {
        "id": None,
        "project_id": proj.id,
        "project_number": proj.project_number or "",
        "operator_status": "",
        "priority": "",
        "tags": "",
        "next_review_date": "",
        "internal_notes": "",
        "report_note": "",
        "created_at": "",
        "updated_at": "",
    }


def _overlay_to_dict(row: ProjectOverlay, *, is_admin: bool = False) -> dict:
    payload = to_dict(row) or {}
    if not is_admin:
        payload["internal_notes"] = ""
    return payload


def project_overlay_payload(sess: Session, proj: Project, *, is_admin: bool = False) -> dict:
    row = sess.scalar(
        select(ProjectOverlay).where(ProjectOverlay.project_id == proj.id)
    )
    if row is not None:
        return _overlay_to_dict(row, is_admin=is_admin)

    row = sess.scalar(
        select(ProjectOverlay).where(ProjectOverlay.project_number == proj.project_number)
    )
    if row is not None and row.project_id in (None, proj.id):
        return _overlay_to_dict(row, is_admin=is_admin)

    # A matching project_number bound to a different project_id is drift.
    # Surface it through sync-status instead of silently attaching it here.
    return _empty_project_overlay(proj)


def project_workspace_payload(sess: Session, proj: Project,
                              *, user_id: int | None = None,
                              is_admin: bool = False,
                              limit: int = 50) -> dict:
    sites = [
        to_dict(row)
        for row in sess.scalars(
            select(ProjectSite)
            .where(ProjectSite.project_id == proj.id)
            .order_by(ProjectSite.is_primary.desc(), ProjectSite.id.asc())
        ).all()
    ]
    linked = {
        "work_tasks": linked_rows(sess, WorkTask, proj.id, proj.project_number,
                                   user_id=user_id, is_admin=is_admin, limit=limit),
        "project_work_tasks": linked_rows(sess, ProjectWorkTask, proj.id, proj.project_number,
                                           user_id=user_id, is_admin=is_admin, limit=limit),
        "training_tasks": linked_rows(sess, TrainingTask, proj.id, proj.project_number,
                                       user_id=user_id, is_admin=is_admin, limit=limit),
        "personnel_issues": linked_rows(sess, PersonnelIssue, proj.id, proj.project_number,
                                         user_id=user_id, is_admin=is_admin, limit=limit),
        "calendar_events": linked_rows(sess, CalendarEvent, proj.id, proj.project_number,
                                        user_id=user_id, is_admin=is_admin, limit=limit),
    }
    return {
        "project": to_dict(proj),
        "sites": sites,
        "linked_records": linked,
        "counts": {key: len(value) for key, value in linked.items()} | {
            "sites": len(sites),
        },
        "external": {
            "system": proj.external_system or "",
            "ref": proj.external_ref or "",
        },
        "operator_overlay": project_overlay_payload(sess, proj, is_admin=is_admin),
        "can_edit_overlay": bool(is_admin),
        "recent_activity": recent_activity_for_linked_records(
            sess, linked, is_admin=is_admin, limit=20,
        ),
        "capabilities_visible": bool(is_admin),
    }


__all__ = ["linked_rows", "project_workspace_payload", "project_overlay_payload", "recent_activity_for_linked_records"]
