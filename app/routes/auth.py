"""Login / register / logout."""
from flask import (
    Blueprint, redirect, render_template, request, session, url_for,
)
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import get_session
from ..models import ApprovedEmail, User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("main.index"))

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
            return redirect(url_for("main.index"))
        error = "Invalid email or password."

    return render_template("login.html", error=error, mode="login")


@bp.route("/register", methods=["GET", "POST"])
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

    return render_template("login.html", error=error, success=success, mode="register")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
