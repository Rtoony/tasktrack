"""Bot-scoped task status updates for the Hermes assistant.

Lets the headless assistant CLOSE (or reopen / re-status) a task, token-
authenticated with the ``bot`` scope. Read paths live in digest.py / agenda.py;
new-item capture goes through /api/v1/inbox. Restricted to the three task
trackers so the agent can't touch personnel/calendar/feedback rows here.

    POST /api/v1/task/<table>/<id>/status      body: {"status": "Complete"}

With no body (or no status), defaults to the table's *done* status — i.e.
"close this task". Status must be in the tracker's status_flow. Every change is
logged to the activity log, attributed to "Hermes".
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from .. import limiter
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import ActivityLog, to_dict
from ..services.tickets import TABLE_MODELS, done_statuses_for_table
from ..tokens import check_scoped_token

bp = Blueprint("agent_tasks", __name__)

TASK_TABLES = ("work_tasks", "project_work_tasks", "training_tasks")


def _skip_limit_for_tests() -> bool:
    return bool(current_app.config.get("TESTING"))


def _done_status(table: str, flow: list) -> str:
    done = done_statuses_for_table(table)
    return next((s for s in flow if s in done), "Complete")


@bp.route("/api/v1/task/<table>/<int:record_id>/status", methods=["POST"])
@limiter.limit("30 per minute; 300 per hour", exempt_when=_skip_limit_for_tests)
def set_status(table, record_id):
    err = check_scoped_token("bot")
    if err:
        return err
    if table not in TASK_TABLES:
        return jsonify({"error": f"status updates limited to {list(TASK_TABLES)}"}), 400

    flow = ALLOWED_TABLES[table].get("status_flow", [])
    requested = (request.get_json(silent=True) or {}).get("status") or ""
    requested = requested.strip() or _done_status(table, flow)
    if flow and requested not in flow:
        return jsonify({"error": f"status must be one of {flow}"}), 400

    sess = get_session()
    Model = TABLE_MODELS[table]
    row = sess.get(Model, record_id)
    if row is None:
        return jsonify({"error": "task not found"}), 404

    old = row.status
    if old == requested:
        return jsonify({"ok": True, "unchanged": True, "task": to_dict(row)})

    row.status = requested
    row.updated_at = datetime.utcnow()
    sess.add(ActivityLog(
        table_name=table, record_id=record_id, action="status_change",
        field_name="status", old_value=str(old), new_value=str(requested),
        user_name="Hermes",
    ))
    sess.commit()
    sess.refresh(row)
    return jsonify({"ok": True, "from": old, "to": requested, "task": to_dict(row)})
