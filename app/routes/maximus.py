"""Maximus token-auth surface — personal tasks (X-Token gated).

Lives in TaskTrack today; Phase 7 will remove this surface entirely
(default) or split it into a tiny dedicated service if Maximus turns
out to depend on it.

Token auth uses the `personal` scope (TASKTRACK_TOKEN_PERSONAL); the
legacy single-secret TASKTRACK_TOKEN is still accepted for one release
with a deprecation log.
"""
from datetime import date

from flask import Blueprint, jsonify, request

from ..db import get_db
from ..services.audit import log_activity
from ..tokens import check_scoped_token

bp = Blueprint("maximus", __name__)


def _require_tasktrack_token():
    return check_scoped_token("personal")


def _personal_task_row_to_dict(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "category": row["category"],
        "priority": row["priority"],
        "status": row["status"],
        "due_date": row["due_date"] or None,
        "recurrence": row["recurrence"] or None,
        "notes": row["notes"] or None,
        "source": row["source"] or None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


@bp.route("/api/v1/maximus/tasks", methods=["GET"])
def maximus_list_tasks():
    err = _require_tasktrack_token()
    if err:
        return err
    db = get_db()
    rows = db.execute(
        "SELECT * FROM personal_tasks WHERE completed_at IS NULL ORDER BY "
        " CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 WHEN 'Low' THEN 2 ELSE 3 END,"
        " COALESCE(NULLIF(due_date, ''), '9999-99-99'), created_at"
    ).fetchall()
    return jsonify({"tasks": [_personal_task_row_to_dict(r) for r in rows]})


@bp.route("/api/v1/maximus/tasks/today", methods=["GET"])
def maximus_tasks_today():
    err = _require_tasktrack_token()
    if err:
        return err
    db = get_db()
    today = date.today().isoformat()
    rows = db.execute(
        "SELECT * FROM personal_tasks "
        "WHERE completed_at IS NULL AND (due_date = ? OR due_date = '' OR due_date < ?) "
        "ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 WHEN 'Low' THEN 2 ELSE 3 END,"
        " created_at",
        (today, today),
    ).fetchall()
    return jsonify({"date": today, "tasks": [_personal_task_row_to_dict(r) for r in rows]})


@bp.route("/api/v1/maximus/tasks/completed", methods=["GET"])
def maximus_tasks_completed():
    err = _require_tasktrack_token()
    if err:
        return err
    limit = max(1, min(request.args.get("limit", default=50, type=int), 500))
    db = get_db()
    rows = db.execute(
        "SELECT * FROM personal_tasks WHERE completed_at IS NOT NULL "
        "ORDER BY completed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return jsonify({"tasks": [_personal_task_row_to_dict(r) for r in rows]})


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

    db = get_db()
    cur = db.execute(
        "INSERT INTO personal_tasks (title, description, category, priority, due_date, recurrence, notes, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (title, description, category, priority, due_date, recurrence, notes, source),
    )
    db.commit()
    row = db.execute("SELECT * FROM personal_tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    log_activity(db, "personal_tasks", row["id"], "created", new=title)
    db.commit()
    return jsonify({"task": _personal_task_row_to_dict(row)}), 201


@bp.route("/api/v1/maximus/tasks/<int:task_id>", methods=["GET"])
def maximus_get_task(task_id):
    err = _require_tasktrack_token()
    if err:
        return err
    db = get_db()
    row = db.execute("SELECT * FROM personal_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({"task": _personal_task_row_to_dict(row)})


@bp.route("/api/v1/maximus/tasks/<int:task_id>", methods=["PUT"])
def maximus_update_task(task_id):
    err = _require_tasktrack_token()
    if err:
        return err
    db = get_db()
    existing = db.execute("SELECT * FROM personal_tasks WHERE id = ?", (task_id,)).fetchone()
    if not existing:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    fields = {}
    for k in ("title", "description", "category", "priority", "status", "due_date", "recurrence", "notes"):
        if k in data:
            fields[k] = (data[k] or "").strip() if isinstance(data[k], str) else data[k]
    if not fields:
        return jsonify({"task": _personal_task_row_to_dict(existing)})
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys()) + ", updated_at = CURRENT_TIMESTAMP"
    params = list(fields.values()) + [task_id]
    db.execute(f"UPDATE personal_tasks SET {set_clause} WHERE id = ?", params)
    db.commit()
    row = db.execute("SELECT * FROM personal_tasks WHERE id = ?", (task_id,)).fetchone()
    log_activity(db, "personal_tasks", task_id, "updated", new=", ".join(fields.keys()))
    db.commit()
    return jsonify({"task": _personal_task_row_to_dict(row)})


@bp.route("/api/v1/maximus/tasks/<int:task_id>/complete", methods=["POST"])
def maximus_complete_task(task_id):
    err = _require_tasktrack_token()
    if err:
        return err
    db = get_db()
    row = db.execute("SELECT * FROM personal_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["completed_at"]:
        return jsonify({"task": _personal_task_row_to_dict(row), "already_complete": True})
    db.execute(
        "UPDATE personal_tasks SET status = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        ("Complete", task_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM personal_tasks WHERE id = ?", (task_id,)).fetchone()
    log_activity(db, "personal_tasks", task_id, "completed")
    db.commit()
    return jsonify({"task": _personal_task_row_to_dict(row)})


@bp.route("/api/v1/maximus/tasks/<int:task_id>", methods=["DELETE"])
def maximus_delete_task(task_id):
    err = _require_tasktrack_token()
    if err:
        return err
    db = get_db()
    row = db.execute("SELECT * FROM personal_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    db.execute("DELETE FROM personal_tasks WHERE id = ?", (task_id,))
    db.commit()
    log_activity(db, "personal_tasks", task_id, "deleted", old=row["title"])
    db.commit()
    return jsonify({"ok": True})


@bp.route("/api/v1/maximus/stats", methods=["GET"])
def maximus_stats():
    err = _require_tasktrack_token()
    if err:
        return err
    db = get_db()
    row = db.execute(
        "SELECT "
        " SUM(CASE WHEN completed_at IS NULL THEN 1 ELSE 0 END) AS active, "
        " SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS done, "
        " SUM(CASE WHEN completed_at IS NULL AND priority = 'High' THEN 1 ELSE 0 END) AS high, "
        " SUM(CASE WHEN completed_at IS NOT NULL AND date(completed_at) = date('now') THEN 1 ELSE 0 END) AS done_today "
        "FROM personal_tasks"
    ).fetchone()
    return jsonify({
        "active": row["active"] or 0,
        "completed": row["done"] or 0,
        "high_priority_active": row["high"] or 0,
        "completed_today": row["done_today"] or 0,
    })
