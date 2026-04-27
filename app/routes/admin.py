"""Admin routes — user/email/role management + Telegram pairing controls."""
import secrets

from flask import (
    Blueprint, jsonify, redirect, render_template, request, session, url_for,
)
from werkzeug.security import generate_password_hash

from ..auth import admin_required
from ..config import ADMIN_WORKFLOW_VIEWS
from ..db import get_db

bp = Blueprint("admin", __name__)


@bp.route("/admin")
@admin_required
def admin_panel():
    db = get_db()
    users = [dict(r) for r in db.execute(
        "SELECT id, email, display_name, role, created_at FROM users ORDER BY id"
    ).fetchall()]
    emails = [dict(r) for r in db.execute(
        "SELECT email, added_at FROM approved_emails ORDER BY email"
    ).fetchall()]
    telegram_link_code = db.execute(
        "SELECT value FROM app_settings WHERE key = 'telegram_link_code'"
    ).fetchone()
    telegram_link_code = telegram_link_code["value"] if telegram_link_code else ""
    telegram_chats = [
        dict(r)
        for r in db.execute(
            "SELECT chat_id, username, display_name, linked_at, last_seen_at, is_active "
            "FROM telegram_chat_access ORDER BY linked_at DESC"
        ).fetchall()
    ]
    workflow_links = [
        {"key": key, "title": meta["title"], "subtitle": meta["subtitle"], "href": f"/admin/workflow/{key}"}
        for key, meta in ADMIN_WORKFLOW_VIEWS.items()
    ]
    return render_template(
        "admin.html",
        users=users,
        approved_emails=emails,
        user_name=session.get("user_name", ""),
        workflow_links=workflow_links,
        telegram_link_code=telegram_link_code,
        telegram_chats=telegram_chats,
    )


@bp.route("/admin/workflow/<workflow>")
@admin_required
def admin_workflow_view(workflow):
    meta = ADMIN_WORKFLOW_VIEWS.get(workflow)
    if not meta:
        return redirect(url_for("admin.admin_panel"))
    return render_template(
        "index.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        standalone_tab=workflow,
        standalone_title=meta["title"],
        standalone_subtitle=meta["subtitle"],
    )


@bp.route("/api/v1/admin/approved-emails", methods=["POST"])
@admin_required
def add_approved_email():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required"}), 400
    db = get_db()
    db.execute("INSERT OR IGNORE INTO approved_emails (email) VALUES (?)", (email,))
    db.commit()
    return jsonify({"added": email}), 201


@bp.route("/api/v1/admin/approved-emails/<path:email>", methods=["DELETE"])
@admin_required
def remove_approved_email(email):
    db = get_db()
    db.execute("DELETE FROM approved_emails WHERE email = ?", (email,))
    db.commit()
    return jsonify({"removed": email})


@bp.route("/api/v1/admin/users/<int:user_id>/role", methods=["PUT"])
@admin_required
def update_user_role(user_id):
    data = request.json or {}
    role = data.get("role", "user")
    if role not in ("admin", "user"):
        return jsonify({"error": "Invalid role"}), 400
    db = get_db()
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    db.commit()
    return jsonify({"updated": user_id, "role": role})


@bp.route("/api/v1/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    if user_id == session.get("user_id"):
        return jsonify({"error": "Cannot delete yourself"}), 400
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return jsonify({"deleted": user_id})


@bp.route("/api/v1/admin/users/<int:user_id>/reset-password", methods=["PUT"])
@admin_required
def reset_user_password(user_id):
    data = request.json or {}
    password = data.get("password", "")
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))
    db.commit()
    return jsonify({"reset": user_id})


@bp.route("/api/v1/admin/telegram/link-code/regenerate", methods=["PUT"])
@admin_required
def regenerate_telegram_link_code():
    code = secrets.token_hex(4).upper()
    db = get_db()
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES ('telegram_link_code', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (code,),
    )
    db.commit()
    return jsonify({"telegram_link_code": code})


@bp.route("/api/v1/admin/telegram/chats/<int:chat_id>", methods=["DELETE"])
@admin_required
def remove_telegram_chat(chat_id):
    db = get_db()
    db.execute("DELETE FROM telegram_chat_access WHERE chat_id = ?", (chat_id,))
    db.commit()
    return jsonify({"removed": chat_id})
