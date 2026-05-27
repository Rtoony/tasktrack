"""Project workspace aggregation shared by registry and reports."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CalendarEvent,
    PersonnelIssue,
    Project,
    ProjectSite,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
    to_dict,
)
from .tickets import record_visible_to_user


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
        "capabilities_visible": bool(is_admin),
    }


__all__ = ["linked_rows", "project_workspace_payload"]
