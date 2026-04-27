"""Maximus token-auth surface — personal tasks (X-Token gated).

Lives in TaskTrack today; Phase 7 will remove this surface entirely
(default) or split it into a tiny dedicated service if Maximus turns
out to depend on it.

Token auth uses the `personal` scope (TASKTRACK_TOKEN_PERSONAL); the
legacy single-secret TASKTRACK_TOKEN is still accepted for one release
with a deprecation log.
"""
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func, select

from ..db import get_session
from ..models import PersonalTask
from ..services.audit import log_activity
from ..tokens import check_scoped_token

bp = Blueprint("maximus", __name__)


def _require_tasktrack_token():
    return check_scoped_token("personal")


# Ordering: High > Medium > Low > everything else.
_PRIORITY_ORDER = case(
    (PersonalTask.priority == "High", 0),
    (PersonalTask.priority == "Medium", 1),
    (PersonalTask.priority == "Low", 2),
    else_=3,
)


def _personal_task_to_dict(row: PersonalTask | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "category": row.category,
        "priority": row.priority,
        "status": row.status,
        "due_date": row.due_date or None,
        "recurrence": row.recurrence or None,
        "notes": row.notes or None,
        "source": row.source or None,
        "created_at": row.created_at.isoformat(sep=" ") if row.created_at else None,
        "updated_at": row.updated_at.isoformat(sep=" ") if row.updated_at else None,
        "completed_at": row.completed_at.isoformat(sep=" ") if row.completed_at else None,
    }


@bp.route("/api/v1/maximus/tasks", methods=["GET"])
def maximus_list_tasks():
    err = _require_tasktrack_token()
    if err:
        return err
    sess = get_session()
    rows = sess.scalars(
        select(PersonalTask)
        .where(PersonalTask.completed_at.is_(None))
        .order_by(
            _PRIORITY_ORDER,
            func.coalesce(func.nullif(PersonalTask.due_date, ""), "9999-99-99"),
            PersonalTask.created_at,
        )
    ).all()
    return jsonify({"tasks": [_personal_task_to_dict(r) for r in rows]})


@bp.route("/api/v1/maximus/tasks/today", methods=["GET"])
def maximus_tasks_today():
    err = _require_tasktrack_token()
    if err:
        return err
    sess = get_session()
    today = date.today().isoformat()
    rows = sess.scalars(
        select(PersonalTask)
        .where(
            PersonalTask.completed_at.is_(None),
            ((PersonalTask.due_date == today)
             | (PersonalTask.due_date == "")
             | (PersonalTask.due_date < today)),
        )
        .order_by(_PRIORITY_ORDER, PersonalTask.created_at)
    ).all()
    return jsonify({"date": today, "tasks": [_personal_task_to_dict(r) for r in rows]})


@bp.route("/api/v1/maximus/tasks/completed", methods=["GET"])
def maximus_tasks_completed():
    err = _require_tasktrack_token()
    if err:
        return err
    limit = max(1, min(request.args.get("limit", default=50, type=int), 500))
    sess = get_session()
    rows = sess.scalars(
        select(PersonalTask)
        .where(PersonalTask.completed_at.is_not(None))
        .order_by(PersonalTask.completed_at.desc())
        .limit(limit)
    ).all()
    return jsonify({"tasks": [_personal_task_to_dict(r) for r in rows]})


@bp.route("/api/v1/maximus/tasks", methods=["POST"])
def maximus_capture_task():
    err = _require_tasktrack_token()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    category = (data.get("category") or "Personal").strip()[:64]
    priority = (data.get("priority") or "Medium").strip()[:16]
    if priority not in ("High", "Medium", "Low"):
        priority = "Medium"
    description = (data.get("description") or "").strip()
    due_date = (data.get("due_date") or "").strip()
    recurrence = (data.get("recurrence") or "").strip()
    notes = (data.get("notes") or "").strip()
    source = (data.get("source") or "maximus").strip()[:64]

    sess = get_session()
    task = PersonalTask(
        title=title,
        description=description,
        category=category,
        priority=priority,
        due_date=due_date,
        recurrence=recurrence,
        notes=notes,
        source=source,
    )
    sess.add(task)
    sess.flush()  # populate task.id
    log_activity(sess, "personal_tasks", task.id, "created", new=title)
    sess.commit()
    sess.refresh(task)  # pick up server-default created_at/updated_at
    return jsonify({"task": _personal_task_to_dict(task)}), 201


@bp.route("/api/v1/maximus/tasks/<int:task_id>", methods=["GET"])
def maximus_get_task(task_id):
    err = _require_tasktrack_token()
    if err:
        return err
    sess = get_session()
    row = sess.get(PersonalTask, task_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"task": _personal_task_to_dict(row)})


@bp.route("/api/v1/maximus/tasks/<int:task_id>", methods=["PUT"])
def maximus_update_task(task_id):
    err = _require_tasktrack_token()
    if err:
        return err
    sess = get_session()
    existing = sess.get(PersonalTask, task_id)
    if existing is None:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    fields_changed = []
    for key in ("title", "description", "category", "priority", "status",
                "due_date", "recurrence", "notes"):
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str):
            value = value.strip()
        setattr(existing, key, value)
        fields_changed.append(key)
    if not fields_changed:
        return jsonify({"task": _personal_task_to_dict(existing)})
    existing.updated_at = datetime.utcnow()
    log_activity(sess, "personal_tasks", task_id, "updated",
                 new=", ".join(fields_changed))
    sess.commit()
    sess.refresh(existing)
    return jsonify({"task": _personal_task_to_dict(existing)})


@bp.route("/api/v1/maximus/tasks/<int:task_id>/complete", methods=["POST"])
def maximus_complete_task(task_id):
    err = _require_tasktrack_token()
    if err:
        return err
    sess = get_session()
    row = sess.get(PersonalTask, task_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    if row.completed_at:
        return jsonify({"task": _personal_task_to_dict(row), "already_complete": True})
    now = datetime.utcnow()
    row.status = "Complete"
    row.completed_at = now
    row.updated_at = now
    log_activity(sess, "personal_tasks", task_id, "completed")
    sess.commit()
    sess.refresh(row)
    return jsonify({"task": _personal_task_to_dict(row)})


@bp.route("/api/v1/maximus/tasks/<int:task_id>", methods=["DELETE"])
def maximus_delete_task(task_id):
    err = _require_tasktrack_token()
    if err:
        return err
    sess = get_session()
    row = sess.get(PersonalTask, task_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    title = row.title
    sess.delete(row)
    log_activity(sess, "personal_tasks", task_id, "deleted", old=title)
    sess.commit()
    return jsonify({"ok": True})


@bp.route("/api/v1/maximus/stats", methods=["GET"])
def maximus_stats():
    err = _require_tasktrack_token()
    if err:
        return err
    sess = get_session()
    active = sess.scalar(
        select(func.count()).select_from(PersonalTask)
        .where(PersonalTask.completed_at.is_(None))
    ) or 0
    done = sess.scalar(
        select(func.count()).select_from(PersonalTask)
        .where(PersonalTask.completed_at.is_not(None))
    ) or 0
    high = sess.scalar(
        select(func.count()).select_from(PersonalTask)
        .where(PersonalTask.completed_at.is_(None),
               PersonalTask.priority == "High")
    ) or 0
    done_today = sess.scalar(
        select(func.count()).select_from(PersonalTask)
        .where(PersonalTask.completed_at.is_not(None),
               func.date(PersonalTask.completed_at) == func.date("now"))
    ) or 0
    return jsonify({
        "active": active,
        "completed": done,
        "high_priority_active": high,
        "completed_today": done_today,
    })
