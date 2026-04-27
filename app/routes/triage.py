"""AI Intake (triage) routes.

`/api/triage` accepts session OR token auth (for email_intake.py + Maximus).
`/api/<table>/<id>/confirm` clears the AI needs_review flag once an
operator has reviewed an AI-generated row.
"""
import os
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from ..db import get_db
from ..services.audit import log_activity
from ..services.tickets import create_direct_record
from ..services.triage import (
    TRIAGE_ALLOWED_TARGETS, TRIAGE_CONFIRM_TABLES, TRIAGE_PRESET_KEYS,
    TRIAGE_TARGET_LABELS, auto_project_number, run_triage,
    triage_plan_to_payload,
)

bp = Blueprint("triage", __name__)

TASKTRACK_TOKEN = os.environ.get("TASKTRACK_TOKEN", "")


def _require_triage_auth():
    """Triage accepts either an active session or a valid TASKTRACK_TOKEN header."""
    if "user_id" in session:
        return None
    if not TASKTRACK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    presented = request.headers.get("X-Token") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if presented and presented == TASKTRACK_TOKEN:
        return None
    return jsonify({"error": "unauthorized"}), 401


@bp.route("/api/triage", methods=["POST"])
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

    db = get_db()
    new_id, create_err = create_direct_record(
        db,
        target,
        payload,
        "AI Intake",
        action="created",
        action_detail=f"AI triage ({model}, {presets['source']}, {TRIAGE_TARGET_LABELS.get(target, target)})",
    )
    if create_err:
        db.rollback()
        return jsonify({"error": create_err}), 400
    db.commit()
    row = db.execute(f"SELECT * FROM {target} WHERE id = ?", (new_id,)).fetchone()
    response["task"] = dict(row)
    response["task_id"] = new_id
    return jsonify(response), 201


@bp.route("/api/<table>/<int:record_id>/confirm", methods=["POST"])
def confirm_ai_task(table, record_id):
    if table not in TRIAGE_CONFIRM_TABLES:
        return jsonify({"error": "confirm not supported for this table"}), 400
    err = _require_triage_auth()
    if err:
        return err
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    db.execute(
        f"UPDATE {table} SET needs_review = 0, updated_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), record_id),
    )
    log_activity(db, table, record_id, "confirmed", new="cleared needs_review flag")
    db.commit()
    updated = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    return jsonify(dict(updated))
