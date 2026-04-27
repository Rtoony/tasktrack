"""Generic CRUD + dashboard + search + comments + activity + export.

Per-tracker behavior is keyed off `ALLOWED_TABLES`. Phase 3 will route
every list/object query through `visible_tickets(user, model)` for
RBAC scoping.
"""
import csv
import io
from datetime import datetime

from flask import (
    Blueprint, Response, jsonify, request, session,
)

from ..auth import login_required
from ..config import ALLOWED_TABLES
from ..db import get_db
from ..services.audit import log_activity
from ..services.tickets import (
    create_direct_record, done_statuses_for_table, extra_create_fields,
    is_overdue_value, overdue_field_for_table, validate_record_data,
)

bp = Blueprint("api", __name__)


# ── Dashboard ───────────────────────────────────────────────────────────────

@bp.route("/api/v1/dashboard")
@login_required
def dashboard_stats():
    db = get_db()
    stats = {}
    for table, cfg in ALLOWED_TABLES.items():
        rows = db.execute(f"SELECT * FROM {table}").fetchall()
        all_rows = [dict(r) for r in rows]
        done_statuses = done_statuses_for_table(table)
        active = [r for r in all_rows if r.get("status") not in done_statuses]
        overdue = []
        due_field = overdue_field_for_table(cfg)
        if due_field:
            overdue = [r for r in active if is_overdue_value(r.get(due_field))]

        by_status = {}
        for r in all_rows:
            s = r.get("status", "Unknown")
            by_status[s] = by_status.get(s, 0) + 1

        by_priority = {}
        p_field = "priority" if "priority" in cfg["fields"] else "severity"
        for r in all_rows:
            p = r.get(p_field, "Medium")
            by_priority[p] = by_priority.get(p, 0) + 1

        stats[table] = {
            "total": len(all_rows),
            "active": len(active),
            "overdue": len(overdue),
            "overdue_items": overdue[:10],
            "by_status": by_status,
            "by_priority": by_priority,
        }

    recent = db.execute(
        "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 20"
    ).fetchall()

    return jsonify({"stats": stats, "recent_activity": [dict(r) for r in recent]})


# ── Search ─────────────────────────────────────────────────────────────────

@bp.route("/api/v1/search")
@login_required
def search_records():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])

    db = get_db()
    results = []
    pattern = f"%{q}%"

    for row in db.execute(
        "SELECT id, 'work_tasks' as source, title as label, description as detail, priority, status, due_date FROM work_tasks "
        "WHERE title LIKE ? OR cad_skill_area LIKE ? OR description LIKE ? OR requested_by LIKE ? OR request_reference LIKE ? OR notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    for row in db.execute(
        "SELECT id, 'project_work_tasks' as source, title as label, task_description as detail, priority, status, due_at as due_date FROM project_work_tasks "
        "WHERE project_name LIKE ? OR title LIKE ? OR project_number LIKE ? OR engineer LIKE ? OR task_description LIKE ? OR notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    for row in db.execute(
        "SELECT id, 'training_tasks' as source, title as label, training_goals as detail, priority, status, due_date FROM training_tasks "
        "WHERE title LIKE ? OR trainees LIKE ? OR requested_by LIKE ? OR skill_area LIKE ? OR training_goals LIKE ? OR additional_context LIKE ? OR notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    for row in db.execute(
        "SELECT id, 'personnel_issues' as source, person_name as label, issue_description as detail, severity as priority, status, follow_up_date as due_date FROM personnel_issues "
        "WHERE person_name LIKE ? OR observed_by LIKE ? OR cad_skill_area LIKE ? OR issue_description LIKE ? OR incident_context LIKE ? OR recommended_training LIKE ? OR resolution_notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    for row in db.execute(
        "SELECT id, 'suggestion_box' as source, title as label, summary as detail, priority, status, '' as due_date FROM suggestion_box "
        "WHERE title LIKE ? OR suggestion_type LIKE ? OR submitted_by LIKE ? OR submitted_for LIKE ? OR summary LIKE ? OR expected_value LIKE ? OR review_notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    return jsonify(results)


# ── Comments ───────────────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/<int:record_id>/comments", methods=["GET"])
@login_required
def list_comments(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    rows = db.execute(
        "SELECT * FROM comments WHERE table_name = ? AND record_id = ? ORDER BY created_at ASC",
        (table, record_id),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.route("/api/v1/<table>/<int:record_id>/comments", methods=["POST"])
@login_required
def add_comment(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    data = request.json or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Comment body is required"}), 400
    user = session.get("user_name", "Unknown")
    db = get_db()
    cur = db.execute(
        "INSERT INTO comments (table_name, record_id, user_name, body) VALUES (?, ?, ?, ?)",
        (table, record_id, user, body),
    )
    log_activity(db, table, record_id, "comment", new=body[:80])
    db.commit()
    row = db.execute("SELECT * FROM comments WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


# ── Quick Status Toggle ────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/<int:record_id>/cycle-status", methods=["PUT"])
@login_required
def cycle_status(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    cfg = ALLOWED_TABLES[table]
    flow = cfg["status_flow"]
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    current = row["status"]
    try:
        idx = flow.index(current)
        new_status = flow[(idx + 1) % len(flow)]
    except ValueError:
        new_status = flow[0]
    db.execute(
        f"UPDATE {table} SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, datetime.utcnow().isoformat(), record_id),
    )
    log_activity(db, table, record_id, "status_change", "status", current, new_status)
    db.commit()
    updated = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    return jsonify(dict(updated))


# ── Activity Log ───────────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/<int:record_id>/activity")
@login_required
def record_activity(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    rows = db.execute(
        "SELECT * FROM activity_log WHERE table_name = ? AND record_id = ? ORDER BY created_at DESC LIMIT 50",
        (table, record_id),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ── CSV Export ─────────────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/export.csv")
@login_required
def export_csv(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    rows = db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    if not rows:
        return Response("No data", mimetype="text/plain")

    output = io.StringIO()
    cols = rows[0].keys()
    writer = csv.DictWriter(output, fieldnames=cols)
    writer.writeheader()
    for r in rows:
        writer.writerow(dict(r))

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={table}_{datetime.utcnow().strftime('%Y%m%d')}.csv"},
    )


# ── CRUD ───────────────────────────────────────────────────────────────────

@bp.route("/api/v1/<table>", methods=["GET"])
@login_required
def list_records(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sort = request.args.get("sort", "id")
    order = request.args.get("order", "asc").upper()
    if order not in ("ASC", "DESC"):
        order = "ASC"
    all_cols = ALLOWED_TABLES[table]["fields"] + ["id", "created_at", "updated_at", "reported_date"]
    if sort not in all_cols:
        sort = "id"
    db = get_db()
    rows = db.execute(f"SELECT * FROM {table} ORDER BY {sort} {order}").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.route("/api/v1/<table>", methods=["POST"])
@login_required
def create_record(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    data = request.json or {}
    cfg = ALLOWED_TABLES[table]
    data.update(extra_create_fields(table, data))
    error = validate_record_data(table, data, creating=True)
    if error:
        return jsonify({"error": error}), 400
    for req in cfg["required"]:
        if not data.get(req, "").strip():
            return jsonify({"error": f"'{req}' is required"}), 400
    allowed_fields = cfg["fields"] + ["created_by_user_id", "created_by_name"]
    fields = [f for f in allowed_fields if f in data]
    vals = [data[f] for f in fields]
    placeholders = ", ".join(["?"] * len(fields))
    col_names = ", ".join(fields)
    db = get_db()
    cur = db.execute(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", vals)
    log_activity(db, table, cur.lastrowid, "created", new=data.get("title") or data.get("person_name", ""))
    db.commit()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@bp.route("/api/v1/<table>/<int:record_id>", methods=["GET"])
@login_required
def get_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


@bp.route("/api/v1/<table>/<int:record_id>", methods=["PUT"])
@login_required
def update_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    data = request.json or {}
    cfg = ALLOWED_TABLES[table]
    error = validate_record_data(table, data)
    if error:
        return jsonify({"error": error}), 400
    fields = [f for f in cfg["fields"] if f in data]
    if not fields:
        return jsonify({"error": "No valid fields to update"}), 400

    db = get_db()
    old_row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if not old_row:
        return jsonify({"error": "Not found"}), 404

    for f in fields:
        old_val = old_row[f] if f in old_row.keys() else ""
        new_val = data[f]
        if str(old_val) != str(new_val):
            log_activity(db, table, record_id, "updated", f, old_val, new_val)

    sets = ", ".join([f"{f} = ?" for f in fields])
    vals = [data[f] for f in fields]
    vals.append(datetime.utcnow().isoformat())
    vals.append(record_id)
    db.execute(f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?", vals)
    db.commit()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    return jsonify(dict(row))


@bp.route("/api/v1/<table>/<int:record_id>", methods=["DELETE"])
@login_required
def delete_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    label = ""
    if row:
        label = row["title"] if "title" in row.keys() else row["person_name"] if "person_name" in row.keys() else ""
    log_activity(db, table, record_id, "deleted", new=label)
    db.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"deleted": record_id})


# ── Suggestion Promotion ───────────────────────────────────────────────────

@bp.route("/api/v1/suggestion_box/<int:record_id>/promote-to-cad", methods=["POST"])
@login_required
def promote_suggestion_to_cad(record_id):
    db = get_db()
    suggestion = db.execute("SELECT * FROM suggestion_box WHERE id = ?", (record_id,)).fetchone()
    if not suggestion:
        return jsonify({"error": "Suggestion not found"}), 404
    if suggestion["promoted_work_task_id"]:
        return jsonify({"error": "Suggestion already promoted"}), 400

    title = (suggestion["title"] or "").strip()
    summary = (suggestion["summary"] or "").strip()
    expected_value = (suggestion["expected_value"] or "").strip()
    submitted_by = (suggestion["submitted_by"] or "").strip()
    suggestion_type = (suggestion["suggestion_type"] or "").strip()

    payload = {
        "title": title,
        "cad_skill_area": suggestion_type,
        "description": summary,
        "requested_by": submitted_by,
        "request_reference": (
            f"Promoted from Suggestion Box #{record_id}\n"
            f"For review by: {suggestion['submitted_for'] or 'General Review'}\n"
            f"Why this would help: {expected_value}"
        ).strip(),
        "priority": suggestion["priority"] or "Medium",
        "status": "Not Started",
        "created_by_user_id": session.get("user_id"),
        "created_by_name": session.get("user_name", ""),
    }
    new_id, error = create_direct_record(
        db,
        "work_tasks",
        payload,
        "Suggestion Promotion",
        action="created",
        action_detail=title,
    )
    if error:
        db.rollback()
        return jsonify({"error": error}), 400

    review_notes = (suggestion["review_notes"] or "").strip()
    if review_notes:
        review_notes += "\n\n"
    review_notes += f"Promoted to CAD Development task #{new_id} on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} by {session.get('user_name', 'Unknown')}."
    db.execute(
        "UPDATE suggestion_box SET status = ?, promoted_work_task_id = ?, review_notes = ?, updated_at = ? WHERE id = ?",
        ("Promoted to CAD", new_id, review_notes, datetime.utcnow().isoformat(), record_id),
    )
    log_activity(db, "suggestion_box", record_id, "promoted", new=f"CAD task #{new_id}")
    db.commit()
    row = db.execute("SELECT * FROM suggestion_box WHERE id = ?", (record_id,)).fetchone()
    return jsonify({"suggestion": dict(row), "work_task_id": new_id})
