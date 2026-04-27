"""Bot-scoped Telegram API.

Endpoints the Telegram bot calls to manage chat pairing, record activity,
and create tickets — all token-authenticated with the `bot` scope so the
bot never needs direct DB access.

- POST /api/v1/telegram/pair    — bind a chat to the global link code
- POST /api/v1/telegram/touch   — record activity, return paired status
- POST /api/v1/telegram/tickets — create a ticket attributed to the chat

Identity mapping (telegram_chat_access.user_id) lands NULL until either
the admin manually assigns a chat to a user or the per-user pairing flow
ships in Phase 3 RBAC.
"""
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from ..config import ALLOWED_TABLES
from ..db import get_db
from ..services.audit import log_activity
from ..services.tickets import create_direct_record
from ..tokens import check_scoped_token

bp = Blueprint("telegram_api", __name__)


def _require_bot():
    return check_scoped_token("bot")


def _chat_status(db, chat_id: int) -> dict:
    row = db.execute(
        "SELECT chat_id, user_id, is_active, display_name, username "
        "FROM telegram_chat_access WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if not row:
        return {"paired": False, "user_id": None}
    return {
        "paired": bool(row["is_active"]),
        "user_id": row["user_id"],
        "display_name": row["display_name"] or "",
        "username": row["username"] or "",
    }


@bp.route("/api/v1/telegram/pair", methods=["POST"])
def telegram_pair():
    err = _require_bot()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id")
    code = (data.get("code") or "").strip().upper()
    username = (data.get("username") or "").strip()[:64]
    display_name = (data.get("display_name") or "").strip()[:128]

    if not isinstance(chat_id, int) or not code:
        return jsonify({"error": "chat_id (int) and code required",
                        "request_id": g.get("request_id", "-")}), 400

    db = get_db()
    expected = db.execute(
        "SELECT value FROM app_settings WHERE key = 'telegram_link_code'"
    ).fetchone()
    if not expected or code != expected["value"].upper():
        return jsonify({"error": "invalid link code",
                        "paired": False,
                        "request_id": g.get("request_id", "-")}), 401

    db.execute(
        "INSERT INTO telegram_chat_access (chat_id, username, display_name, is_active) "
        "VALUES (?, ?, ?, 1) "
        "ON CONFLICT(chat_id) DO UPDATE SET "
        "  username = excluded.username, "
        "  display_name = excluded.display_name, "
        "  is_active = 1, "
        "  last_seen_at = CURRENT_TIMESTAMP",
        (chat_id, username, display_name),
    )
    db.commit()
    return jsonify({"ok": True, **_chat_status(db, chat_id)}), 200


@bp.route("/api/v1/telegram/touch", methods=["POST"])
def telegram_touch():
    err = _require_bot()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id")
    username = (data.get("username") or "").strip()[:64]
    display_name = (data.get("display_name") or "").strip()[:128]

    if not isinstance(chat_id, int):
        return jsonify({"error": "chat_id (int) required",
                        "request_id": g.get("request_id", "-")}), 400

    db = get_db()
    db.execute(
        "UPDATE telegram_chat_access SET "
        "  last_seen_at = CURRENT_TIMESTAMP, "
        "  username = COALESCE(NULLIF(?, ''), username), "
        "  display_name = COALESCE(NULLIF(?, ''), display_name) "
        "WHERE chat_id = ?",
        (username, display_name, chat_id),
    )
    db.commit()
    return jsonify(_chat_status(db, chat_id))


@bp.route("/api/v1/telegram/tickets", methods=["POST"])
def telegram_create_ticket():
    err = _require_bot()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id")
    table = (data.get("table") or "").strip()
    payload = data.get("payload") or {}

    if not isinstance(chat_id, int):
        return jsonify({"error": "chat_id (int) required",
                        "request_id": g.get("request_id", "-")}), 400
    if table not in ALLOWED_TABLES:
        return jsonify({"error": f"invalid table: {table}",
                        "request_id": g.get("request_id", "-")}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "payload must be an object",
                        "request_id": g.get("request_id", "-")}), 400

    db = get_db()
    chat = db.execute(
        "SELECT chat_id, user_id, is_active, display_name "
        "FROM telegram_chat_access WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if not chat or not chat["is_active"]:
        return jsonify({"error": "chat is not paired — send /link CODE first",
                        "request_id": g.get("request_id", "-")}), 403

    # Attribute ticket to the chat's bound user (when set) and tag the
    # creator name so the audit log shows the Telegram origin.
    payload = dict(payload)  # don't mutate caller's dict
    payload["created_by_user_id"] = chat["user_id"]
    payload["created_by_name"] = (
        f"Telegram ({chat['display_name'] or chat_id})" if chat["user_id"] is None
        else (chat["display_name"] or f"Telegram #{chat_id}")
    )
    if "status" not in payload or not str(payload.get("status", "")).strip():
        payload["status"] = ALLOWED_TABLES[table]["status_flow"][0]
    if "priority" in ALLOWED_TABLES[table]["fields"] and not payload.get("priority"):
        payload["priority"] = "Medium"
    payload["source"] = payload.get("source") or "telegram"

    new_id, error = create_direct_record(
        db,
        table,
        payload,
        "Telegram bot",
        action="created",
        action_detail=f"Telegram chat {chat_id}",
    )
    if error:
        db.rollback()
        return jsonify({"error": error,
                        "request_id": g.get("request_id", "-")}), 400
    db.commit()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (new_id,)).fetchone()
    return jsonify({"ok": True, "task": dict(row), "task_id": new_id}), 201
