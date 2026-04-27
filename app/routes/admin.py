"""Admin routes — user/email/role management + Telegram pairing controls."""
import secrets

from flask import (
    Blueprint, jsonify, redirect, render_template, request, session, url_for,
)
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from ..auth import admin_required
from ..config import ADMIN_WORKFLOW_VIEWS
from ..db import get_session
from ..models import ApprovedEmail, AppSetting, TelegramChatAccess, User

bp = Blueprint("admin", __name__)


@bp.route("/admin")
@admin_required
def admin_panel():
    sess = get_session()
    users = [
        {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "role": u.role,
            "created_at": u.created_at.isoformat(sep=" ") if u.created_at else None,
        }
        for u in sess.scalars(select(User).order_by(User.id)).all()
    ]
    emails = [
        {
            "email": ae.email,
            "added_at": ae.added_at.isoformat(sep=" ") if ae.added_at else None,
        }
        for ae in sess.scalars(select(ApprovedEmail).order_by(ApprovedEmail.email)).all()
    ]
    code_setting = sess.get(AppSetting, "telegram_link_code")
    telegram_link_code = code_setting.value if code_setting else ""
    telegram_chats = [
        {
            "chat_id": c.chat_id,
            "username": c.username,
            "display_name": c.display_name,
            "linked_at": c.linked_at.isoformat(sep=" ") if c.linked_at else None,
            "last_seen_at": c.last_seen_at.isoformat(sep=" ") if c.last_seen_at else None,
            "is_active": c.is_active,
        }
        for c in sess.scalars(
            select(TelegramChatAccess).order_by(TelegramChatAccess.linked_at.desc())
        ).all()
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
    sess = get_session()
    existing = sess.get(ApprovedEmail, email)
    if existing is None:
        sess.add(ApprovedEmail(email=email))
        sess.commit()
    return jsonify({"added": email}), 201


@bp.route("/api/v1/admin/approved-emails/<path:email>", methods=["DELETE"])
@admin_required
def remove_approved_email(email):
    sess = get_session()
    existing = sess.get(ApprovedEmail, email)
    if existing is not None:
        sess.delete(existing)
        sess.commit()
    return jsonify({"removed": email})


@bp.route("/api/v1/admin/users/<int:user_id>/role", methods=["PUT"])
@admin_required
def update_user_role(user_id):
    data = request.json or {}
    role = data.get("role", "user")
    if role not in ("admin", "user"):
        return jsonify({"error": "Invalid role"}), 400
    sess = get_session()
    user = sess.get(User, user_id)
    if user is None:
        return jsonify({"error": "Not found"}), 404
    user.role = role
    sess.commit()
    return jsonify({"updated": user_id, "role": role})


@bp.route("/api/v1/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    if user_id == session.get("user_id"):
        return jsonify({"error": "Cannot delete yourself"}), 400
    sess = get_session()
    user = sess.get(User, user_id)
    if user is not None:
        sess.delete(user)
        sess.commit()
    return jsonify({"deleted": user_id})


@bp.route("/api/v1/admin/users/<int:user_id>/reset-password", methods=["PUT"])
@admin_required
def reset_user_password(user_id):
    data = request.json or {}
    password = data.get("password", "")
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    sess = get_session()
    user = sess.get(User, user_id)
    if user is None:
        return jsonify({"error": "Not found"}), 404
    user.password_hash = generate_password_hash(password)
    sess.commit()
    return jsonify({"reset": user_id})


@bp.route("/api/v1/admin/telegram/link-code/regenerate", methods=["PUT"])
@admin_required
def regenerate_telegram_link_code():
    code = secrets.token_hex(4).upper()
    sess = get_session()
    setting = sess.get(AppSetting, "telegram_link_code")
    if setting is None:
        sess.add(AppSetting(key="telegram_link_code", value=code))
    else:
        setting.value = code
    sess.commit()
    return jsonify({"telegram_link_code": code})


@bp.route("/api/v1/admin/telegram/chats/<int:chat_id>", methods=["DELETE"])
@admin_required
def remove_telegram_chat(chat_id):
    sess = get_session()
    chat = sess.get(TelegramChatAccess, chat_id)
    if chat is not None:
        sess.delete(chat)
        sess.commit()
    return jsonify({"removed": chat_id})
