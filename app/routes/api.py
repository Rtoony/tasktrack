"""Generic CRUD + dashboard + search + comments + activity + export.

Per-tracker behavior is keyed off `ALLOWED_TABLES`. Phase 3 will route
every list/object query through `visible_tickets(user, model)` for
RBAC scoping.
"""
import csv
import io
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, jsonify, request, session
from sqlalchemy import desc, or_, select, text

from ..auth import login_required
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import (
    ActivityLog,
    CalendarEvent,
    Comment,
    InboxItem,
    PersonalItem,
    PersonnelIssue,
    Project,
    to_dict,
)
from ..services.audit import log_activity
from ..services.intake_reports import intake_source_report
from ..services.tickets import (
    TABLE_MODELS,
    done_statuses_for_table,
    enrich_with_fks,
    extra_create_fields,
    can_view_record_detail,
    is_overdue_value,
    overdue_field_for_table,
    record_to_user_dict,
    record_visible_to_user,
    validate_record_data,
)

bp = Blueprint("api", __name__)


def _is_admin() -> bool:
    return session.get("user_role") == "admin"


def _record_visible_to_current_user(table: str, row) -> bool:
    return record_visible_to_user(table, row, session.get("user_id"))


def _record_detail_visible_to_current_user(table: str, row) -> bool:
    return can_view_record_detail(
        table, row, session.get("user_id"), is_admin=_is_admin()
    )


def _record_to_current_user_dict(table: str, row) -> dict:
    return record_to_user_dict(
        table, row, session.get("user_id"), is_admin=_is_admin()
    )


def _target_detail_visible(sess, table: str, record_id: int) -> bool:
    Model = TABLE_MODELS.get(table)
    if Model is None:
        return False
    row = sess.get(Model, record_id)
    return row is not None and _record_detail_visible_to_current_user(table, row)


PROTECTED_ACTIVITY_TABLES = {"calendar_events", "personnel_issues"}


def _activity_visible_to_current_user(sess, row: ActivityLog) -> bool:
    Model = TABLE_MODELS.get(row.table_name)
    if Model is None:
        return True
    target = sess.get(Model, row.record_id)
    if target is None:
        # Deleted protected rows no longer carry enough context to prove
        # owner/private visibility, so keep those audit values admin-only.
        if row.table_name in PROTECTED_ACTIVITY_TABLES:
            return _is_admin()
        return True
    return _record_detail_visible_to_current_user(row.table_name, target)


# ── Dashboard ───────────────────────────────────────────────────────────────

def _date_from_due_value(raw_value):
    if not raw_value:
        return None

    value = str(raw_value).strip()
    if not value:
        return None

    try:
        if "T" in value:
            return datetime.fromisoformat(value).date()
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _is_due_soon_value(raw_value, *, days: int = 14) -> bool:
    due_on = _date_from_due_value(raw_value)
    if due_on is None:
        return False
    today = date.today()
    return today <= due_on <= today + timedelta(days=days)


def _dashboard_record_title(table: str, row: dict) -> str:
    if table == "personnel_issues":
        return (
            row.get("title")
            or row.get("issue_description")
            or row.get("person_name")
            or f"#{row.get('id', '?')}"
        )
    return (
        row.get("title")
        or row.get("project_name")
        or row.get("name")
        or row.get("summary")
        or f"#{row.get('id', '?')}"
    )


def _dashboard_activity_dict(sess, row: ActivityLog) -> dict:
    payload = to_dict(row) or {}
    cfg = ALLOWED_TABLES.get(row.table_name) or {}
    payload["label"] = cfg.get("label") or row.table_name
    payload["record_title"] = ""
    Model = TABLE_MODELS.get(row.table_name)
    if Model is None:
        return payload
    target = sess.get(Model, row.record_id)
    if target is None or not _record_detail_visible_to_current_user(row.table_name, target):
        return payload
    payload["record_title"] = _dashboard_record_title(
        row.table_name,
        _record_to_current_user_dict(row.table_name, target),
    )
    return payload


@bp.route("/api/v1/dashboard")
@login_required
def dashboard_stats():
    sess = get_session()
    stats = {}
    for table, cfg in ALLOWED_TABLES.items():
        Model = TABLE_MODELS[table]
        model_rows = [
            r for r in sess.scalars(select(Model)).all()
            if _record_visible_to_current_user(table, r)
        ]
        all_rows = [_record_to_current_user_dict(table, r) for r in model_rows]
        done_statuses = done_statuses_for_table(table)
        active = [r for r in all_rows if r.get("status") not in done_statuses]
        overdue = []
        due_field = overdue_field_for_table(cfg)
        if due_field:
            overdue = [r for r in active if is_overdue_value(r.get(due_field))]
        due_soon = []
        if due_field:
            due_soon = [
                r for r in active
                if not is_overdue_value(r.get(due_field))
                and _is_due_soon_value(r.get(due_field))
            ]

        by_status = {}
        for r in all_rows:
            s = r.get("status", "Unknown")
            by_status[s] = by_status.get(s, 0) + 1

        by_priority = {}
        p_field = "priority" if "priority" in cfg["fields"] else "severity"
        for r in all_rows:
            p = r.get(p_field, "Medium")
            by_priority[p] = by_priority.get(p, 0) + 1

        by_category = {}
        if "category" in cfg["fields"]:
            for r in all_rows:
                cat = r.get("category") or "Uncategorized"
                bucket = by_category.setdefault(cat, {
                    "total": 0, "active": 0, "overdue": 0, "due_soon": 0, "by_status": {}
                })
                bucket["total"] += 1
                status = r.get("status", "Unknown")
                bucket["by_status"][status] = bucket["by_status"].get(status, 0) + 1
                if r.get("status") not in done_statuses:
                    bucket["active"] += 1
                    if due_field and is_overdue_value(r.get(due_field)):
                        bucket["overdue"] += 1
                    elif due_field and _is_due_soon_value(r.get(due_field)):
                        bucket["due_soon"] += 1

        stats[table] = {
            "total": len(all_rows),
            "active": len(active),
            "overdue": len(overdue),
            "overdue_items": overdue[:10],
            "due_soon": len(due_soon),
            "due_soon_items": due_soon[:10],
            "by_status": by_status,
            "by_priority": by_priority,
            "by_category": by_category,
        }

    recent = [
        _dashboard_activity_dict(sess, r)
        for r in sess.scalars(
            select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(50)
        ).all()
        if _activity_visible_to_current_user(sess, r)
    ][:20]
    intake = intake_source_report(
        sess,
        sources=["web-form", "paper-form", "remarkable-ocr"],
        days=30,
        limit=25,
        needs_review=True,
    )
    return jsonify({"stats": stats, "recent_activity": recent, "intake": intake})


# ── Search ─────────────────────────────────────────────────────────────────
#
# Keeps raw text SQL via session.execute() — the per-table column
# projections + UNION-shape don't translate cleanly to ORM and the
# JS frontend reads the aliased keys (source/label/detail/...).

_SEARCH_SQLS = (
    text(
        "SELECT id, 'work_tasks' as source, title as label, description as detail, "
        "priority, status, due_date FROM work_tasks "
        "WHERE title LIKE :p ESCAPE '\\' OR cad_skill_area LIKE :p ESCAPE '\\' "
        "OR description LIKE :p ESCAPE '\\' OR requested_by LIKE :p ESCAPE '\\' "
        "OR request_reference LIKE :p ESCAPE '\\' OR notes LIKE :p ESCAPE '\\' "
        "LIMIT 20"
    ),
    text(
        "SELECT id, 'project_work_tasks' as source, title as label, "
        "task_description as detail, priority, status, due_at as due_date "
        "FROM project_work_tasks "
        "WHERE project_name LIKE :p ESCAPE '\\' OR title LIKE :p ESCAPE '\\' "
        "OR project_number LIKE :p ESCAPE '\\' OR engineer LIKE :p ESCAPE '\\' "
        "OR task_description LIKE :p ESCAPE '\\' OR notes LIKE :p ESCAPE '\\' "
        "OR scope_notes LIKE :p ESCAPE '\\' OR progress_notes LIKE :p ESCAPE '\\' "
        "OR confirmation_notes LIKE :p ESCAPE '\\' OR completion_notes LIKE :p ESCAPE '\\' "
        "LIMIT 20"
    ),
    text(
        "SELECT id, 'training_tasks' as source, title as label, "
        "training_goals as detail, priority, status, due_date FROM training_tasks "
        "WHERE title LIKE :p ESCAPE '\\' OR trainees LIKE :p ESCAPE '\\' "
        "OR requested_by LIKE :p ESCAPE '\\' OR skill_area LIKE :p ESCAPE '\\' "
        "OR training_goals LIKE :p ESCAPE '\\' OR additional_context LIKE :p ESCAPE '\\' "
        "OR notes LIKE :p ESCAPE '\\' "
        "LIMIT 20"
    ),
)


def _escape_like(q: str) -> str:
    """Escape LIKE wildcards so user input matches literally (with ESCAPE '\\')."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_personnel_issues(sess, pattern: str) -> list[dict]:
    """Admin-only capability search; non-admin reports stay narrative-safe."""
    if not _is_admin():
        return []
    stmt = select(PersonnelIssue).where(
        or_(
            PersonnelIssue.person_name.ilike(pattern, escape="\\"),
            PersonnelIssue.observed_by.ilike(pattern, escape="\\"),
            PersonnelIssue.cad_skill_area.ilike(pattern, escape="\\"),
            PersonnelIssue.issue_description.ilike(pattern, escape="\\"),
            PersonnelIssue.incident_context.ilike(pattern, escape="\\"),
            PersonnelIssue.recommended_training.ilike(pattern, escape="\\"),
            PersonnelIssue.resolution_notes.ilike(pattern, escape="\\"),
        )
    ).order_by(PersonnelIssue.id.desc()).limit(20)
    return [
        {
            "id": row.id,
            "source": "personnel_issues",
            "label": row.person_name or "Capability note",
            "detail": row.issue_description or "",
            "priority": row.severity,
            "status": row.status,
            "due_date": row.follow_up_date or "",
        }
        for row in sess.scalars(stmt).all()
    ]


def _search_calendar_events(sess, pattern: str) -> list[dict]:
    """Calendar search path with the same private-event guard as CRUD."""
    user_id = session.get("user_id")
    stmt = select(CalendarEvent).where(
        or_(
            CalendarEvent.title.ilike(pattern, escape="\\"),
            CalendarEvent.description.ilike(pattern, escape="\\"),
            CalendarEvent.event_type.ilike(pattern, escape="\\"),
            CalendarEvent.project_number.ilike(pattern, escape="\\"),
            CalendarEvent.location.ilike(pattern, escape="\\"),
        )
    )
    if user_id is None:
        stmt = stmt.where(CalendarEvent.visibility != "private")
    else:
        stmt = stmt.where(
            or_(
                CalendarEvent.visibility != "private",
                CalendarEvent.created_by_user_id == user_id,
            )
        )
    rows = sess.scalars(stmt.order_by(CalendarEvent.start_at.asc()).limit(20)).all()
    results = []
    for row in rows:
        if not _record_visible_to_current_user("calendar_events", row):
            continue
        detail_bits = [
            bit for bit in (row.event_type, row.project_number, row.location)
            if bit
        ]
        results.append({
            "id": row.id,
            "source": "calendar_events",
            "label": row.title,
            "detail": row.description or " · ".join(detail_bits),
            "priority": row.event_type,
            "status": row.status,
            "due_date": row.start_at,
        })
    return results


def _search_personal_items(sess, pattern: str) -> list[dict]:
    """Internal-queue search; category rides along so the UI picks the right tab."""
    stmt = select(PersonalItem).where(
        or_(
            PersonalItem.title.ilike(pattern, escape="\\"),
            PersonalItem.body.ilike(pattern, escape="\\"),
            PersonalItem.source_ref.ilike(pattern, escape="\\"),
        )
    ).order_by(PersonalItem.id.desc()).limit(20)
    return [
        {
            "id": row.id,
            "source": "personal_items",
            "label": row.title,
            "detail": row.body or "",
            "priority": row.priority,
            "status": row.status,
            "due_date": row.due_date or "",
            "category": row.category or "",
        }
        for row in sess.scalars(stmt).all()
    ]


def _search_inbox_items(sess, pattern: str) -> list[dict]:
    """Triage search; archived captures stay out of the dropdown."""
    stmt = select(InboxItem).where(
        InboxItem.status != "Archived",
        or_(
            InboxItem.title.ilike(pattern, escape="\\"),
            InboxItem.body.ilike(pattern, escape="\\"),
        ),
    ).order_by(InboxItem.id.desc()).limit(20)
    return [
        {
            "id": row.id,
            "source": "inbox_items",
            "label": row.title,
            "detail": row.body or "",
            "priority": row.priority,
            "status": row.status,
            "due_date": row.due_date or "",
        }
        for row in sess.scalars(stmt).all()
    ]


def _search_projects(sess, pattern: str) -> list[dict]:
    """Registry search; project_number rides along so the UI opens the workspace."""
    stmt = select(Project).where(
        Project.active == 1,
        or_(
            Project.project_number.ilike(pattern, escape="\\"),
            Project.name.ilike(pattern, escape="\\"),
        ),
    ).order_by(Project.project_number.asc()).limit(20)
    return [
        {
            "id": row.id,
            "source": "projects",
            "label": (f"{row.project_number} — {row.name}" if row.name else row.project_number),
            "detail": row.client or row.component or "",
            "priority": "",
            "status": row.display_status,
            "due_date": "",
            "project_number": row.project_number,
        }
        for row in sess.scalars(stmt).all()
    ]


@bp.route("/api/v1/search")
@login_required
def search_records():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    sess = get_session()
    pattern = f"%{_escape_like(q)}%"
    results = []
    for stmt in _SEARCH_SQLS:
        for row in sess.execute(stmt, {"p": pattern}).mappings().all():
            results.append(dict(row))
    results.extend(_search_personnel_issues(sess, pattern))
    results.extend(_search_calendar_events(sess, pattern))
    results.extend(_search_personal_items(sess, pattern))
    results.extend(_search_inbox_items(sess, pattern))
    results.extend(_search_projects(sess, pattern))
    return jsonify(results)


# ── Comments ───────────────────────────────────────────────────────────────

@bp.route("/api/v1/<table>/<int:record_id>/comments", methods=["GET"])
@login_required
def list_comments(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sess = get_session()
    if not _target_detail_visible(sess, table, record_id):
        return jsonify({"error": "Not found"}), 404
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
    if not _target_detail_visible(sess, table, record_id):
        return jsonify({"error": "Not found"}), 404
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
    if row is None or not _record_detail_visible_to_current_user(table, row):
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
    if not _target_detail_visible(sess, table, record_id):
        return jsonify({"error": "Not found"}), 404
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
    rows = [
        r for r in sess.scalars(select(Model).order_by(Model.id)).all()
        if _record_detail_visible_to_current_user(table, r)
    ]
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
    rows = [r for r in sess.scalars(stmt).all() if _record_detail_visible_to_current_user(table, r)]
    return jsonify([to_dict(r) for r in rows])


@bp.route("/api/v1/<table>", methods=["POST"])
@login_required
def create_record(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    data = request.json or {}
    cfg = ALLOWED_TABLES[table]
    sess = get_session()
    data.update(extra_create_fields(table, data))
    error = validate_record_data(table, data, creating=True, sess=sess)
    if error:
        return jsonify({"error": error}), 400
    for req in cfg["required"]:
        if not str(data.get(req, "")).strip():
            return jsonify({"error": f"'{req}' is required"}), 400
    Model = TABLE_MODELS[table]
    valid_cols = {c.name for c in Model.__table__.columns}
    kwargs = {k: v for k, v in data.items() if k in valid_cols}
    record = Model(**kwargs)
    if table == "feedback_items" and kwargs.get("status") in done_statuses_for_table("feedback_items"):
        record.completed_at = datetime.utcnow()
    sess.add(record)
    sess.flush()
    # Phase-0: best-effort FK enrichment from text columns. Quiet no-op
    # for tables without an _FK_ENRICHMENT entry.
    enrich_with_fks(sess, table, record)
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
    if row is None or not _record_detail_visible_to_current_user(table, row):
        return jsonify({"error": "Not found"}), 404
    return jsonify(to_dict(row))


@bp.route("/api/v1/<table>/<int:record_id>", methods=["PUT"])
@login_required
def update_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    data = request.json or {}
    cfg = ALLOWED_TABLES[table]
    Model = TABLE_MODELS[table]
    sess = get_session()
    row = sess.get(Model, record_id)
    if row is None or not _record_detail_visible_to_current_user(table, row):
        return jsonify({"error": "Not found"}), 404

    validation_data = dict(data)
    if table == "calendar_events":
        # Partial updates still need to validate the resulting event window.
        validation_data.setdefault("start_at", getattr(row, "start_at", "") or "")
        validation_data.setdefault("end_at", getattr(row, "end_at", "") or "")

    error = validate_record_data(table, validation_data, sess=sess)
    if error:
        return jsonify({"error": error}), 400
    for key in list(data.keys()):
        if key in validation_data:
            data[key] = validation_data[key]
    if table == "project_work_tasks":
        for key in ("project_id", "project_number", "project_name"):
            if key in validation_data:
                data[key] = validation_data[key]

    fields = [f for f in cfg["fields"] if f in data]
    if not fields:
        return jsonify({"error": "No valid fields to update"}), 400

    for f in fields:
        old_val = getattr(row, f, "")
        new_val = data[f]
        if str(old_val) != str(new_val):
            action = "status_change" if f == "status" else "updated"
            log_activity(sess, table, record_id, action, f, old_val, new_val)
        setattr(row, f, new_val)

    if table == "feedback_items" and "status" in fields:
        if getattr(row, "status", "") in done_statuses_for_table("feedback_items"):
            row.completed_at = datetime.utcnow()
        else:
            row.completed_at = None

    enrich_with_fks(sess, table, row, refresh_existing=True, changed_fields=set(fields))
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
    if row is None or not _record_detail_visible_to_current_user(table, row):
        return jsonify({"error": "Not found"}), 404
    label = ""
    if row is not None:
        label = getattr(row, "title", None) or getattr(row, "person_name", "") or ""
        sess.delete(row)
    log_activity(sess, table, record_id, "deleted", new=label)
    sess.commit()
    return jsonify({"deleted": record_id})


