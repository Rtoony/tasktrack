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
from sqlalchemy import select

from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import AppSetting, TelegramChatAccess, to_dict
from ..services.tickets import TABLE_MODELS, create_direct_record
from ..tokens import check_scoped_token

bp = Blueprint("telegram_api", __name__)


def _require_bot():
    return check_scoped_token("bot")


def _chat_status(sess, chat_id: int) -> dict:
    chat = sess.get(TelegramChatAccess, chat_id)
    if chat is None:
        return {"paired": False, "user_id": None}
    return {
        "paired": bool(chat.is_active),
        "user_id": chat.user_id,
        "display_name": chat.display_name or "",
        "username": chat.username or "",
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

    sess = get_session()
    expected_code = sess.scalar(
        select(AppSetting.value).where(AppSetting.key == "telegram_link_code")
    )
    if not expected_code or code != expected_code.upper():
        return jsonify({"error": "invalid link code",
                        "paired": False,
                        "request_id": g.get("request_id", "-")}), 401

    chat = sess.get(TelegramChatAccess, chat_id)
    now = datetime.utcnow()
    if chat is None:
        chat = TelegramChatAccess(
            chat_id=chat_id,
            username=username,
            display_name=display_name,
            is_active=1,
        )
        sess.add(chat)
    else:
        chat.username = username
        chat.display_name = display_name
        chat.is_active = 1
        chat.last_seen_at = now
    sess.commit()
    return jsonify({"ok": True, **_chat_status(sess, chat_id)}), 200


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

    sess = get_session()
    chat = sess.get(TelegramChatAccess, chat_id)
    if chat is not None:
        chat.last_seen_at = datetime.utcnow()
        if username:
            chat.username = username
        if display_name:
            chat.display_name = display_name
        sess.commit()
    return jsonify(_chat_status(sess, chat_id))


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

    sess = get_session()
    chat = sess.get(TelegramChatAccess, chat_id)
    if chat is None or not chat.is_active:
        return jsonify({"error": "chat is not paired — send /link CODE first",
                        "request_id": g.get("request_id", "-")}), 403

    # Attribute ticket to the chat's bound user (when set) and tag the
    # creator name so the audit log shows the Telegram origin.
    payload = dict(payload)  # don't mutate caller's dict
    payload["created_by_user_id"] = chat.user_id
    payload["created_by_name"] = (
        f"Telegram ({chat.display_name or chat_id})" if chat.user_id is None
        else (chat.display_name or f"Telegram #{chat_id}")
    )
    if "status" not in payload or not str(payload.get("status", "")).strip():
        payload["status"] = ALLOWED_TABLES[table]["status_flow"][0]
    if "priority" in ALLOWED_TABLES[table]["fields"] and not payload.get("priority"):
        payload["priority"] = "Medium"
    payload["source"] = payload.get("source") or "telegram"

    new_id, error = create_direct_record(
        sess,
        table,
        payload,
        "Telegram bot",
        action="created",
        action_detail=f"Telegram chat {chat_id}",
    )
    if error:
        sess.rollback()
        return jsonify({"error": error,
                        "request_id": g.get("request_id", "-")}), 400
    sess.commit()
    Model = TABLE_MODELS[table]
    row = sess.get(Model, new_id)
    return jsonify({"ok": True, "task": to_dict(row), "task_id": new_id}), 201
