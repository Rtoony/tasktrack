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


@bp.route("/capture/ocr")
@login_required
def capture_ocr():
    return render_template(
        "capture_ocr.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


@bp.route("/testing")
@login_required
def testing_checklist():
    return render_template(
        "testing_checklist.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


@bp.route("/healthz")
def healthz():
    return "ok"
