"""Weekly aggregation route (Phase 6).

- GET /api/v1/weekly?days=7   — JSON snapshot
- GET /weekly?days=7          — HTML render

Both gates behind @login_required. The HTML view honours admin role to
decide whether to include the skill_score_changes block; the JSON
endpoint follows the same rule. `days` clamps to [1, 90].
"""
from flask import Blueprint, jsonify, render_template, request, session

from ..auth import login_required
from ..db import get_session
from ..services.weekly import weekly_snapshot

bp = Blueprint("weekly", __name__)


def _days_arg() -> int:
    raw = request.args.get("days", default="7")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 7
    return max(1, min(v, 90))


def _is_admin() -> bool:
    return session.get("user_role") == "admin"


@bp.route("/api/v1/weekly", methods=["GET"])
@login_required
def weekly_json():
    sess = get_session()
    return jsonify(weekly_snapshot(
        sess, days=_days_arg(), include_admin=_is_admin(),
        user_id=session.get("user_id"),
    ))


@bp.route("/weekly", methods=["GET"])
@login_required
def weekly_page():
    sess = get_session()
    snapshot = weekly_snapshot(
        sess, days=_days_arg(), include_admin=_is_admin(),
        user_id=session.get("user_id"),
    )
    return render_template(
        "weekly.html",
        snapshot=snapshot,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        is_admin=_is_admin(),
    )
