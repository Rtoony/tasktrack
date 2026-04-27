"""AI Intake (triage) routes.

`/api/v1/triage` accepts session OR token auth (for email_intake.py).
`/api/v1/<table>/<id>/confirm` clears the AI needs_review flag once an
operator has reviewed an AI-generated row.

Token auth uses the `triage` scope (TASKTRACK_TOKEN_TRIAGE); the legacy
single-secret TASKTRACK_TOKEN is still accepted for one release with
a deprecation log.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from ..db import get_session
from ..models import to_dict
from ..services.audit import log_activity
from ..services.tickets import TABLE_MODELS, create_direct_record
from ..services.triage import (
    TRIAGE_ALLOWED_TARGETS, TRIAGE_CONFIRM_TABLES, TRIAGE_PRESET_KEYS,
    TRIAGE_TARGET_LABELS, auto_project_number, run_triage,
    triage_plan_to_payload,
)
from ..tokens import check_scoped_token

bp = Blueprint("triage", __name__)


def _require_triage_auth():
    """Either active session or `triage`-scoped token (or legacy token)."""
    if "user_id" in session:
        return None
    return check_scoped_token("triage")


@bp.route("/api/v1/triage", methods=["POST"])
def triage_endpoint():
    err = _require_triage_auth()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    raw_text = (data.get("text") or "").strip()
    if not raw_text:
        return jsonify({"error": "text is required"}), 400
    commit = bool(data.get("commit"))

    target = (data.get("target_table") or "work_tasks").strip() or "work_tasks"
    if target not in TRIAGE_ALLOWED_TARGETS:
        return jsonify({"error": f"invalid target_table: {target}"}), 400

    presets = {k: data.get(k) for k in TRIAGE_PRESET_KEYS if data.get(k) not in (None, "")}
    if "source" not in presets:
        presets["source"] = "paste"
    presets["source"] = str(presets["source"])[:32]

    try:
        plan, model = run_triage(raw_text, target=target, presets=presets)
    except RuntimeError as exc:
        return jsonify({"error": "triage failed", "detail": str(exc)}), 502

    response = {
        "plan": plan,
        "model": model,
        "source": presets["source"],
        "target_table": target,
    }
    if target == "project_work_tasks":
        detected = auto_project_number(raw_text)
        if detected:
            response["detected_project_number"] = detected

    if not commit:
        return jsonify(response)

    payload = triage_plan_to_payload(plan, raw_text, model, target, presets)
    payload["created_by_name"] = session.get("user_name") or f"AI Intake ({presets['source']})"
    payload["created_by_user_id"] = session.get("user_id")

    sess = get_session()
    new_id, create_err = create_direct_record(
        sess,
        target,
        payload,
        "AI Intake",
        action="created",
        action_detail=f"AI triage ({model}, {presets['source']}, {TRIAGE_TARGET_LABELS.get(target, target)})",
    )
    if create_err:
        sess.rollback()
        return jsonify({"error": create_err}), 400
    sess.commit()
    Model = TABLE_MODELS[target]
    row = sess.get(Model, new_id)
    response["task"] = to_dict(row)
    response["task_id"] = new_id
    return jsonify(response), 201


@bp.route("/api/v1/<table>/<int:record_id>/confirm", methods=["POST"])
def confirm_ai_task(table, record_id):
    if table not in TRIAGE_CONFIRM_TABLES:
        return jsonify({"error": "confirm not supported for this table"}), 400
    err = _require_triage_auth()
    if err:
        return err
    sess = get_session()
    Model = TABLE_MODELS[table]
    row = sess.get(Model, record_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    row.needs_review = 0
    row.updated_at = datetime.utcnow()
    log_activity(sess, table, record_id, "confirmed", new="cleared needs_review flag")
    sess.commit()
    sess.refresh(row)
    return jsonify(to_dict(row))
