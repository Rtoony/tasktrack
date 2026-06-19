"""Bot-scoped comment write-back for the Hermes assistant.

The cookie-only comment API (api.add_comment, @login_required) is the human
surface. This is the headless-agent equivalent: it lets Hermes post a progress
note / report-back onto a record it's working — e.g. "started the fix", "blocked
on X", "deployed to lab" — so the operator sees the agent's reasoning inline on
the task instead of only in Telegram.

    POST /api/v1/comment/<table>/<id>   body: {"body": "...", "audience": ""}

Token-authenticated with the ``bot`` scope. Restricted to the same tracker set
the change-feed exposes (the three task trackers + feedback_items), so the agent
can't write onto personnel/calendar/personal rows. Every comment is also written
to the activity log attributed to "Hermes", which means it flows back out through
/api/v1/changes — closing the two-way loop.
"""
from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, current_app, jsonify, request

from .. import limiter
from ..db import get_session
from ..models import ActivityLog, Comment, to_dict
from ..tokens import check_scoped_token

bp = Blueprint("agent_comments", __name__)

# Same allow-list as the change-feed: task trackers + feedback only.
COMMENT_TABLES = ("work_tasks", "project_work_tasks", "training_tasks",
                  "feedback_items")

_MAX_BODY = 8000


def _skip_limit_for_tests() -> bool:
    return bool(current_app.config.get("TESTING"))


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@bp.route("/api/v1/comment/<table>/<int:record_id>", methods=["POST"])
@limiter.limit("30 per minute; 300 per hour", exempt_when=_skip_limit_for_tests)
def add_comment(table, record_id):
    err = check_scoped_token("bot")
    if err:
        return err
    if table not in COMMENT_TABLES:
        return jsonify(
            {"error": f"comments limited to {list(COMMENT_TABLES)}"}), 400

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "comment body is required"}), 400
    if len(body) > _MAX_BODY:
        return jsonify(
            {"error": f"comment body exceeds {_MAX_BODY} chars"}), 400

    # Mirror api.add_comment's clamp: only the known audience values are allowed
    # so an arbitrary value can't leak into the thread.
    audience = (data.get("audience") or "").strip().lower()
    if audience not in ("", "ai-dev"):
        audience = ""

    from ..services.tickets import TABLE_MODELS
    sess = get_session()
    Model = TABLE_MODELS.get(table)
    if Model is None or sess.get(Model, record_id) is None:
        return jsonify({"error": "record not found"}), 404

    comment = Comment(
        table_name=table,
        record_id=record_id,
        user_name="Hermes",
        body=body,
        audience=audience,
    )
    sess.add(comment)
    sess.flush()
    sess.add(ActivityLog(
        table_name=table, record_id=record_id, action="comment",
        field_name="", old_value="",
        new_value=("[AI Dev] " if audience == "ai-dev" else "") + body[:80],
        user_name="Hermes",
        created_at=_utcnow_naive(),
    ))
    sess.commit()
    sess.refresh(comment)
    return jsonify({"ok": True, "comment": to_dict(comment)}), 201
