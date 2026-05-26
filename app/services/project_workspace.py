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


def linked_rows(sess: Session, model, project_id: int, project_number: str,
                *, user_id: int | None = None, limit: int = 50) -> list[dict]:
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
    return [
        to_dict(row) for row in rows
        if record_visible_to_user(model.__tablename__, row, user_id)
    ]


def project_workspace_payload(sess: Session, proj: Project,
                              *, user_id: int | None = None,
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
                                   user_id=user_id, limit=limit),
        "project_work_tasks": linked_rows(sess, ProjectWorkTask, proj.id, proj.project_number,
                                           user_id=user_id, limit=limit),
        "training_tasks": linked_rows(sess, TrainingTask, proj.id, proj.project_number,
                                       user_id=user_id, limit=limit),
        "personnel_issues": linked_rows(sess, PersonnelIssue, proj.id, proj.project_number,
                                         user_id=user_id, limit=limit),
        "calendar_events": linked_rows(sess, CalendarEvent, proj.id, proj.project_number,
                                        user_id=user_id, limit=limit),
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
    }


__all__ = ["linked_rows", "project_workspace_payload"]
