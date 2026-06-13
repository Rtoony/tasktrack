"""Bot-scoped feedback triage + close-the-loop for the Hermes co-developer.

Read path lets the headless assistant triage in-app feedback (what's open, where
in the app, what type) so it can scope dev-jobs. Write path lets it move an item
to Triaged / Fixed / etc. with an optional resolution note when a proposed change
lands. Token-authenticated with the ``bot`` scope; every change is logged to the
activity log, attributed to "Hermes".

Note the gate: "Fixed" is NOT a terminal status here (done = Accepted / Closed /
Won't Fix / Archived). The agent marks a fix as *landed*; Josh accepts/closes it.
The session-authed UI feedback API lives in api.py / feedback.py — this module is
the headless-agent surface only.

    GET  /api/v1/feedback?status=open&type=Bug&limit=50
    POST /api/v1/feedback/<id>/status   body: {"status": "Fixed", "resolution_notes": "..."}
"""
from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from .. import limiter
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import ActivityLog, Comment, FeedbackItem, to_dict
from ..services.tickets import TABLE_MODELS, done_statuses_for_table
from ..tokens import check_scoped_token

bp = Blueprint("agent_feedback", __name__)

_FLOW = ALLOWED_TABLES["feedback_items"]["status_flow"]
_MAX_LIMIT = 200


def _skip_limit_for_tests() -> bool:
    return bool(current_app.config.get("TESTING"))


def _utcnow_naive() -> datetime:
    # Naive UTC to match the schema's CURRENT_TIMESTAMP convention, without the
    # deprecated datetime.utcnow().
    return datetime.now(UTC).replace(tzinfo=None)


def _brief(row) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "feedback_type": row.feedback_type,
        "priority": row.priority,
        "status": row.status,
        "page_url": row.page_url,
        "tab": row.tab,
        "component_label": row.component_label,
        "body": row.body,
        "tags": row.tags,
        "resolution_notes": row.resolution_notes,
        "created_at": str(row.created_at) if row.created_at else None,
    }


@bp.route("/api/v1/feedback", methods=["GET"])
@limiter.limit("60 per minute; 600 per hour", exempt_when=_skip_limit_for_tests)
def list_feedback():
    """Triage view of in-app feedback.

    Query params: ``status`` = ``open`` (default; excludes terminal statuses) /
    ``all`` / a specific status. ``type`` = filter by feedback_type. ``limit``.
    """
    err = check_scoped_token("bot")
    if err:
        return err

    sess = get_session()
    done = set(done_statuses_for_table("feedback_items"))
    status = (request.args.get("status") or "open").strip()
    ftype = (request.args.get("type") or "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), _MAX_LIMIT))
    except (TypeError, ValueError):
        limit = 50

    stmt = select(FeedbackItem)
    if status == "open" and done:
        stmt = stmt.where(FeedbackItem.status.notin_(done))
    elif status not in ("open", "all", ""):
        stmt = stmt.where(FeedbackItem.status == status)
    if ftype:
        stmt = stmt.where(FeedbackItem.feedback_type == ftype)
    stmt = stmt.order_by(FeedbackItem.created_at.desc()).limit(limit)
    items = [_brief(r) for r in sess.execute(stmt).scalars().all()]

    by_type: dict = {}
    by_status: dict = {}
    open_count = 0
    for s, t in sess.execute(select(FeedbackItem.status, FeedbackItem.feedback_type)).all():
        by_status[s] = by_status.get(s, 0) + 1
        by_type[t] = by_type.get(t, 0) + 1
        if s not in done:
            open_count += 1

    return jsonify({
        "generated_at": _utcnow_naive().isoformat(timespec="seconds") + "Z",
        "filter": {"status": status, "type": ftype or None, "limit": limit},
        "counts": {"open": open_count, "total": sum(by_status.values()),
                   "by_type": by_type, "by_status": by_status},
        "items": items,
    })


@bp.route("/api/v1/ai-instructions", methods=["GET"])
@limiter.limit("60 per minute; 600 per hour", exempt_when=_skip_limit_for_tests)
def list_ai_instructions():
    """Feedback #42: instructions an operator addressed to the AI developer.

    Returns comments tagged ``audience='ai-dev'`` (newest first) across every
    record, so the headless co-developer can pull what the human wants built /
    changed and scope a dev-job against the specific record it was left on.
    Optional ``table`` / ``record_id`` narrow it to one record; ``limit`` caps.
    Token-authenticated with the ``bot`` scope.
    """
    err = check_scoped_token("bot")
    if err:
        return err

    sess = get_session()
    table = (request.args.get("table") or "").strip()
    record_id = request.args.get("record_id")
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), _MAX_LIMIT))
    except (TypeError, ValueError):
        limit = 50

    stmt = select(Comment).where(Comment.audience == "ai-dev")
    if table:
        stmt = stmt.where(Comment.table_name == table)
    if record_id is not None:
        try:
            stmt = stmt.where(Comment.record_id == int(record_id))
        except (TypeError, ValueError):
            return jsonify({"error": "record_id must be an integer"}), 400
    # Bound the scan so a pathological ai-dev comment volume can't be fully
    # materialised; the result is still capped at `limit` after the archived filter.
    stmt = stmt.order_by(Comment.created_at.desc()).limit(max(limit * 5, limit))

    # #42 (pre-merge review): skip AI-dev comments whose parent record has been
    # archived — an archived task's instructions are stale, so Hermes shouldn't pull
    # them. Filter then cap (so archived rows don't eat the limit).
    def _parent_archived(comment) -> bool:
        Model = TABLE_MODELS.get(comment.table_name)
        if Model is None or "archived_at" not in {col.name for col in Model.__table__.columns}:
            return False
        parent = sess.get(Model, comment.record_id)
        return parent is not None and parent.archived_at is not None

    instructions = []
    for c in sess.execute(stmt).scalars().all():
        if _parent_archived(c):
            continue
        instructions.append({
            "id": c.id,
            "table_name": c.table_name,
            "record_id": c.record_id,
            "user_name": c.user_name,
            "body": c.body,
            "created_at": str(c.created_at) if c.created_at else None,
            "record_url": f"/?tab={c.table_name}&record={c.record_id}",
        })
        if len(instructions) >= limit:
            break
    return jsonify({
        "generated_at": _utcnow_naive().isoformat(timespec="seconds") + "Z",
        "filter": {"table": table or None, "record_id": record_id, "limit": limit},
        "count": len(instructions),
        "instructions": instructions,
    })


@bp.route("/api/v1/feedback/<int:record_id>/status", methods=["POST"])
@limiter.limit("30 per minute; 300 per hour", exempt_when=_skip_limit_for_tests)
def set_feedback_status(record_id):
    """Re-status a feedback item (e.g. mark a landed fix). body: {status, resolution_notes?}.

    No status defaults to "Fixed" — the agent records that a fix landed; the
    terminal accept/close stays Josh's call.
    """
    err = check_scoped_token("bot")
    if err:
        return err

    data = request.get_json(silent=True) or {}
    requested = (data.get("status") or "").strip() or "Fixed"
    if requested not in _FLOW:
        return jsonify({"error": f"status must be one of {_FLOW}"}), 400

    sess = get_session()
    row = sess.get(FeedbackItem, record_id)
    if row is None:
        return jsonify({"error": "feedback item not found"}), 404

    done = set(done_statuses_for_table("feedback_items"))
    old = row.status
    changed = False
    if old != requested:
        row.status = requested
        changed = True
        sess.add(ActivityLog(
            table_name="feedback_items", record_id=record_id, action="status_change",
            field_name="status", old_value=str(old), new_value=str(requested),
            user_name="Hermes",
        ))
        # audit #26: clear completed_at on reopen, mirroring api.update_record
        row.completed_at = _utcnow_naive() if requested in done else None

    notes = data.get("resolution_notes")
    if notes is not None and str(notes) != (row.resolution_notes or ""):
        old_notes = row.resolution_notes
        row.resolution_notes = str(notes)
        changed = True
        sess.add(ActivityLog(
            table_name="feedback_items", record_id=record_id, action="edit",
            field_name="resolution_notes", old_value=str(old_notes),
            new_value=str(notes), user_name="Hermes",
        ))

    if changed:
        row.updated_at = _utcnow_naive()
        sess.commit()
        sess.refresh(row)
    return jsonify({"ok": True, "from": old, "to": row.status, "changed": changed,
                    "item": to_dict(row)})
