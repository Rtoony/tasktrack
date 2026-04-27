"""Top-level dashboard + healthcheck."""
from flask import Blueprint, render_template, session

from ..auth import login_required

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        standalone_tab=None,
        standalone_title=None,
        standalone_subtitle=None,
    )


@bp.route("/healthz")
def healthz():
    return "ok"
