"""Login / register / logout."""
from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from .. import limiter
from ..db import get_session
from ..models import ApprovedEmail, User

bp = Blueprint("auth", __name__)


def _safe_next_url(raw: str | None) -> str:
    target = (raw or "").strip()
    if not target or not target.startswith("/"):
        return ""
    if target.startswith("//") or target.startswith("/\\"):
        return ""
    return target


def _skip_limit_for_tests() -> bool:
    """Bypass rate limits inside the pytest test client."""
    return bool(current_app.config.get("TESTING"))


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 100 per hour", methods=["POST"],
               exempt_when=_skip_limit_for_tests)
def login():
    next_url = _safe_next_url(request.values.get("next"))
    if "user_id" in session:
        return redirect(next_url or url_for("main.index"))

    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        sess = get_session()
        # email is COLLATE NOCASE in the schema; use func.lower() so
        # SQLAlchemy doesn't add quotes around the COLLATE keyword.
        user = sess.scalar(
            select(User).where(func.lower(User.email) == email)
        )
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session["user_email"] = user.email
            session["user_name"] = user.display_name
            session["user_role"] = user.role
            return redirect(next_url or url_for("main.index"))
        error = "Invalid email or password."

    return render_template("login.html", error=error, mode="login", next_url=next_url)


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour; 20 per day", methods=["POST"],
               exempt_when=_skip_limit_for_tests)
def register():
    if "user_id" in session:
        return redirect(url_for("main.index"))

    error = None
    success = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""

        if not email or not name or not password:
            error = "All fields are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            sess = get_session()
            approved = sess.scalar(
                select(ApprovedEmail).where(func.lower(ApprovedEmail.email) == email)
            )
            if approved is None:
                error = "This email is not on the approved list. Ask the admin to add you."
            else:
                existing = sess.scalar(
                    select(User).where(func.lower(User.email) == email)
                )
                if existing is not None:
                    error = "An account with this email already exists. Try logging in."
                else:
                    sess.add(User(
                        email=email,
                        display_name=name,
                        password_hash=generate_password_hash(password),
                        role="user",
                    ))
                    sess.commit()
                    success = "Account created! You can now log in."

    return render_template("login.html", error=error, success=success, mode="register", next_url="")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
