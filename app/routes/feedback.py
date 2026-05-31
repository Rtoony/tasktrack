"""Feedback management surface for TaskTrack testing and operations."""
from flask import Blueprint, render_template, session

from ..auth import login_required

bp = Blueprint("feedback", __name__)


@bp.route("/feedback")
@login_required
def feedback_page():
    return render_template(
        "feedback.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )
