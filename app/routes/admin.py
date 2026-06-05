"""Admin routes — user/email/role management + Telegram pairing controls."""
import secrets

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from ..auth import admin_required, login_required
from ..config import ADMIN_WORKFLOW_VIEWS
from ..db import get_session
from ..models import (
    ApprovedEmail,
    AppSetting,
    Employee,
    ManagedOption,
    ManagedOptionSet,
    Project,
    TelegramChatAccess,
    User,
)
from ..services.managed_options import (
    create_option,
    create_set,
    get_set,
    list_sets,
    option_payload,
    options_payload,
    set_payload,
    update_option,
    update_set,
)

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


@bp.route("/admin/people")
@admin_required
def admin_people():
    """Seed/manage the Employees registry."""
    sess = get_session()
    employees = sess.scalars(
        select(Employee).order_by(Employee.active.desc(), Employee.display_name.asc())
    ).all()
    return render_template(
        "admin_registry.html",
        kind="employees",
        title="Employees",
        rows=employees,
        user_name=session.get("user_name", ""),
    )


@bp.route("/admin/projects")
@admin_required
def admin_projects():
    """Seed/manage the Projects registry."""
    sess = get_session()
    projects = sess.scalars(
        select(Project).order_by(Project.active.desc(), Project.project_number.asc())
    ).all()
    return render_template(
        "admin_registry.html",
        kind="projects",
        title="Projects",
        rows=projects,
        user_name=session.get("user_name", ""),
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


@bp.route("/api/v1/options/<set_key>", methods=["GET"])
@login_required
def list_managed_option_values(set_key):
    sess = get_session()
    include_inactive = (
        session.get("user_role") == "admin"
        and request.args.get("include_inactive") in ("1", "true", "yes")
    )
    row = get_set(sess, set_key, include_inactive=include_inactive)
    if row is None:
        return jsonify({"error": "option set not found"}), 404
    payload = options_payload(sess, row.key, include_inactive=include_inactive)
    sess.commit()
    return jsonify(payload)


@bp.route("/api/v1/admin/options/sets", methods=["GET"])
@admin_required
def admin_list_option_sets():
    sess = get_session()
    include_inactive = request.args.get("include_inactive", "1") not in ("0", "false", "no")
    rows = [
        set_payload(sess, row, include_options=True, include_inactive_options=True)
        for row in list_sets(sess, include_inactive=include_inactive)
    ]
    sess.commit()
    return jsonify(rows)


@bp.route("/api/v1/admin/options/sets", methods=["POST"])
@admin_required
def admin_create_option_set():
    sess = get_session()
    result = create_set(sess, request.get_json(silent=True) or {})
    if isinstance(result, tuple):
        return jsonify({"error": result[1]}), 400
    sess.commit()
    return jsonify(set_payload(sess, result, include_options=True)), 201


@bp.route("/api/v1/admin/options/sets/<set_key>", methods=["PATCH"])
@admin_required
def admin_update_option_set(set_key):
    sess = get_session()
    row = get_set(sess, set_key, include_inactive=True)
    if row is None:
        return jsonify({"error": "option set not found"}), 404
    error = update_set(row, request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    sess.commit()
    return jsonify(set_payload(sess, row, include_options=True))


@bp.route("/api/v1/admin/options/sets/<set_key>", methods=["DELETE"])
@admin_required
def admin_delete_option_set(set_key):
    sess = get_session()
    row = get_set(sess, set_key, include_inactive=True)
    if row is None:
        return jsonify({"deleted": set_key})
    if row.is_system:
        return jsonify({"error": "system option sets can be deactivated, not deleted"}), 400
    row.active = 0
    sess.commit()
    return jsonify({"deleted": row.key})


@bp.route("/api/v1/admin/options/sets/<set_key>/options", methods=["POST"])
@admin_required
def admin_create_option(set_key):
    sess = get_session()
    set_row = get_set(sess, set_key, include_inactive=True)
    if set_row is None:
        return jsonify({"error": "option set not found"}), 404
    result = create_option(sess, set_row, request.get_json(silent=True) or {})
    if isinstance(result, tuple):
        return jsonify({"error": result[1]}), 400
    sess.commit()
    return jsonify(option_payload(result, set_key=set_row.key)), 201


@bp.route("/api/v1/admin/options/options/<int:option_id>", methods=["PATCH"])
@admin_required
def admin_update_option(option_id):
    sess = get_session()
    row = sess.get(ManagedOption, option_id)
    if row is None:
        return jsonify({"error": "option not found"}), 404
    data = request.get_json(silent=True) or {}
    if "value" in data:
        value = str(data.get("value") or "").strip()
        dupe = sess.scalar(select(ManagedOption).where(
            ManagedOption.set_id == row.set_id,
            ManagedOption.value == value,
            ManagedOption.id != row.id,
        ))
        if dupe is not None:
            return jsonify({"error": "option value already exists in this set"}), 400
    error = update_option(row, data)
    if error:
        return jsonify({"error": error}), 400
    set_row = sess.get(ManagedOptionSet, row.set_id)
    sess.commit()
    return jsonify(option_payload(row, set_key=set_row.key if set_row else ""))


@bp.route("/api/v1/admin/options/options/<int:option_id>", methods=["DELETE"])
@admin_required
def admin_delete_option(option_id):
    sess = get_session()
    row = sess.get(ManagedOption, option_id)
    if row is not None:
        row.active = 0
        sess.commit()
    return jsonify({"deleted": option_id})


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
    # 8 bytes -> 64 bits of entropy (~1.8e19 codes). Plus the
    # /api/v1/telegram/pair rate limit (5/min, 30/hr) this makes
    # brute-force pairing infeasible.
    code = secrets.token_hex(8).upper()
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
