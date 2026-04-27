"""Auth decorators used by routes.

Two flavors:
- @login_required — redirects browsers to /login, returns 401 JSON for API calls.
- @admin_required — redirects non-authed to /login, non-admins to /.

Endpoints are blueprint-prefixed: 'auth.login' and 'main.index'.
"""
from functools import wraps

from flask import jsonify, redirect, request, session, url_for


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        if session.get("user_role") != "admin":
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated
