"""Generic CRUD + dashboard + search + comments + activity + export.

Per-tracker behavior is keyed off `ALLOWED_TABLES`. Phase 3 will route
every list/object query through `visible_tickets(user, model)` for
RBAC scoping.
"""
import csv
import io
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, session
from sqlalchemy import desc, select, text

from ..auth import login_required
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import ActivityLog, Comment, Suggestion, WorkTask, to_dict
from ..services.audit import log_activity
from ..services.tickets import (
    TABLE_MODELS, create_direct_record, done_statuses_for_table,
    extra_create_fields, is_overdue_value, overdue_field_for_table,
    validate_record_data,
)

bp = Blueprint("api", __name__)


# ── Dashboard ───────────────────────────────────────────────────────────────

@bp.route("/api/v1/dashboard")
@login_required
def dashboard_stats():
    sess = get_session()
    stats = {}
    for table, cfg in ALLOWED_TABLES.items():
        Model = TABLE_MODELS[table]
        all_rows = [to_dict(r) for r in sess.scalars(select(Model)).all()]
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

    recent = [
        to_dict(r)
        for r in sess.scalars(
            select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(20)
        ).all()
    ]
    return jsonify({"stats": stats, "recent_activity": recent})


# ── Search ─────────────────────────────────────────────────────────────────
#
# Keeps raw text SQL via session.execute() — the per-table column
# projections + UNION-shape don't translate cleanly to ORM and the
# JS frontend reads the aliased keys (source/label/detail/...).

_SEARCH_SQLS = (
    text(
        "SELECT id, 'work_tasks' as source, title as label, description as detail, "
        "priority, status, due_date FROM work_tasks "
        "WHERE title LIKE :p OR cad_skill_area LIKE :p OR description LIKE :p "
        "OR requested_by LIKE :p OR request_reference LIKE :p OR notes LIKE :p"
    ),
    text(
        "SELECT id, 'project_work_tasks' as source, title as label, "
        "task_description as detail, priority, status, due_at as due_date "
        "FROM project_work_tasks "
        "WHERE project_name LIKE :p OR title LIKE :p OR project_number LIKE :p "
        "OR engineer LIKE :p OR task_description LIKE :p OR notes LIKE :p"
    ),
    text(
        "SELECT id, 'training_tasks' as source, title as label, "
        "training_goals as detail, priority, status, due_date FROM training_tasks "
        "WHERE title LIKE :p OR trainees LIKE :p OR requested_by LIKE :p "
        "OR skill_area LIKE :p OR training_goals LIKE :p OR additional_context LIKE :p OR notes LIKE :p"
    ),
    text(
        "SELECT id, 'personnel_issues' as source, person_name as label, "
        "issue_description as detail, severity as priority, status, "
        "follow_up_date as due_date FROM personnel_issues "
        "WHERE person_name LIKE :p OR observed_by LIKE :p OR cad_skill_area LIKE :p "
        "OR issue_description LIKE :p OR incident_context LIKE :p "
        "OR recommended_training LIKE :p OR resolution_notes LIKE :p"
    ),
    text(
        "SELECT id, 'suggestion_box' as source, title as label, summary as detail, "
        "priority, status, '' as due_date FROM suggestion_box "
        "WHERE title LIKE :p OR suggestion_type LIKE :p OR submitted_by LIKE :p "
        "OR submitted_for LIKE :p OR summary LIKE :p OR expected_value LIKE :p "
        "OR review_notes LIKE :p"
    ),
)


@bp.route("/api/v1/search")
@login_required
def search_records():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    sess = get_session()
    pattern = f"%{q}%"
    results = []
    for stmt in _SEARCH_SQLS:
        for row in sess.execute(stmt, {"p": pattern}).mappings().all():
            results.append(dict(row))
    return jsonify(results)


# ── Comments ───────────────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/<int:record_id>/comments", methods=["GET"])
@login_required
def list_comments(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sess = get_session()
    rows = sess.scalars(
        select(Comment)
        .where(Comment.table_name == table, Comment.record_id == record_id)
        .order_by(Comment.created_at.asc())
    ).all()
    return jsonify([to_dict(r) for r in rows])


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
    sess = get_session()
    comment = Comment(
        table_name=table,
        record_id=record_id,
        user_name=user,
        body=body,
    )
    sess.add(comment)
    sess.flush()
    log_activity(sess, table, record_id, "comment", new=body[:80])
    sess.commit()
    sess.refresh(comment)
    return jsonify(to_dict(comment)), 201


# ── Quick Status Toggle ────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/<int:record_id>/cycle-status", methods=["PUT"])
@login_required
def cycle_status(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    cfg = ALLOWED_TABLES[table]
    flow = cfg["status_flow"]
    sess = get_session()
    Model = TABLE_MODELS[table]
    row = sess.get(Model, record_id)
    if row is None:
        return jsonify({"error": "Not found"}), 404
    current = row.status
    try:
        idx = flow.index(current)
        new_status = flow[(idx + 1) % len(flow)]
    except ValueError:
        new_status = flow[0]
    row.status = new_status
    row.updated_at = datetime.utcnow()
    log_activity(sess, table, record_id, "status_change", "status", current, new_status)
    sess.commit()
    sess.refresh(row)
    return jsonify(to_dict(row))


# ── Activity Log ───────────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/<int:record_id>/activity")
@login_required
def record_activity(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sess = get_session()
    rows = sess.scalars(
        select(ActivityLog)
        .where(ActivityLog.table_name == table, ActivityLog.record_id == record_id)
        .order_by(ActivityLog.created_at.desc())
        .limit(50)
    ).all()
    return jsonify([to_dict(r) for r in rows])


# ── CSV Export ─────────────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/export.csv")
@login_required
def export_csv(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sess = get_session()
    Model = TABLE_MODELS[table]
    rows = sess.scalars(select(Model).order_by(Model.id)).all()
    if not rows:
        return Response("No data", mimetype="text/plain")

    cols = [c.name for c in Model.__table__.columns]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=cols)
    writer.writeheader()
    for r in rows:
        writer.writerow(to_dict(r))

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
    Model = TABLE_MODELS[table]
    sort = request.args.get("sort", "id")
    order = request.args.get("order", "asc").lower()
    if order not in ("asc", "desc"):
        order = "asc"
    valid_cols = {c.name for c in Model.__table__.columns}
    if sort not in valid_cols:
        sort = "id"
    sess = get_session()
    sort_col = getattr(Model, sort)
    stmt = select(Model).order_by(desc(sort_col) if order == "desc" else sort_col)
    rows = sess.scalars(stmt).all()
    return jsonify([to_dict(r) for r in rows])


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
    Model = TABLE_MODELS[table]
    valid_cols = {c.name for c in Model.__table__.columns}
    kwargs = {k: v for k, v in data.items() if k in valid_cols}
    sess = get_session()
    record = Model(**kwargs)
    sess.add(record)
    sess.flush()
    log_activity(sess, table, record.id, "created",
                 new=data.get("title") or data.get("person_name", ""))
    sess.commit()
    sess.refresh(record)
    return jsonify(to_dict(record)), 201


@bp.route("/api/v1/<table>/<int:record_id>", methods=["GET"])
@login_required
def get_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    Model = TABLE_MODELS[table]
    sess = get_session()
    row = sess.get(Model, record_id)
    if row is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(to_dict(row))


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

    Model = TABLE_MODELS[table]
    sess = get_session()
    row = sess.get(Model, record_id)
    if row is None:
        return jsonify({"error": "Not found"}), 404

    for f in fields:
        old_val = getattr(row, f, "")
        new_val = data[f]
        if str(old_val) != str(new_val):
            log_activity(sess, table, record_id, "updated", f, old_val, new_val)
        setattr(row, f, new_val)

    row.updated_at = datetime.utcnow()
    sess.commit()
    sess.refresh(row)
    return jsonify(to_dict(row))


@bp.route("/api/v1/<table>/<int:record_id>", methods=["DELETE"])
@login_required
def delete_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    Model = TABLE_MODELS[table]
    sess = get_session()
    row = sess.get(Model, record_id)
    label = ""
    if row is not None:
        label = getattr(row, "title", None) or getattr(row, "person_name", "") or ""
        sess.delete(row)
    log_activity(sess, table, record_id, "deleted", new=label)
    sess.commit()
    return jsonify({"deleted": record_id})


# ── Suggestion Promotion ───────────────────────────────────────────────────

@bp.route("/api/v1/suggestion_box/<int:record_id>/promote-to-cad", methods=["POST"])
@login_required
def promote_suggestion_to_cad(record_id):
    sess = get_session()
    suggestion = sess.get(Suggestion, record_id)
    if suggestion is None:
        return jsonify({"error": "Suggestion not found"}), 404
    if suggestion.promoted_work_task_id:
        return jsonify({"error": "Suggestion already promoted"}), 400

    title = (suggestion.title or "").strip()
    summary = (suggestion.summary or "").strip()
    expected_value = (suggestion.expected_value or "").strip()
    submitted_by = (suggestion.submitted_by or "").strip()
    suggestion_type = (suggestion.suggestion_type or "").strip()

    payload = {
        "title": title,
        "cad_skill_area": suggestion_type,
        "description": summary,
        "requested_by": submitted_by,
        "request_reference": (
            f"Promoted from Suggestion Box #{record_id}\n"
            f"For review by: {suggestion.submitted_for or 'General Review'}\n"
            f"Why this would help: {expected_value}"
        ).strip(),
        "priority": suggestion.priority or "Medium",
        "status": "Not Started",
        "created_by_user_id": session.get("user_id"),
        "created_by_name": session.get("user_name", ""),
    }
    new_id, error = create_direct_record(
        sess,
        "work_tasks",
        payload,
        "Suggestion Promotion",
        action="created",
        action_detail=title,
    )
    if error:
        sess.rollback()
        return jsonify({"error": error}), 400

    review_notes = (suggestion.review_notes or "").strip()
    if review_notes:
        review_notes += "\n\n"
    review_notes += (
        f"Promoted to CAD Development task #{new_id} on "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} "
        f"by {session.get('user_name', 'Unknown')}."
    )
    suggestion.status = "Promoted to CAD"
    suggestion.promoted_work_task_id = new_id
    suggestion.review_notes = review_notes
    suggestion.updated_at = datetime.utcnow()
    log_activity(sess, "suggestion_box", record_id, "promoted", new=f"CAD task #{new_id}")
    sess.commit()
    sess.refresh(suggestion)
    return jsonify({"suggestion": to_dict(suggestion), "work_task_id": new_id})
