"""Pipeline health pill endpoint (Phase 5).

Reads the cached snapshot from app.services.health — never blocks on a
network call. A slow or down upstream affects the next probe iteration
but the request that polls this endpoint returns instantly.

Public-ish: requires login (so we don't leak component names to the
open internet), but not admin — every logged-in user sees the pill.
"""
from flask import Blueprint, jsonify

from ..auth import login_required
from ..services.health import current_state

bp = Blueprint("health_pill", __name__)


@bp.route("/api/v1/health/pill", methods=["GET"])
@login_required
def health_pill():
    return jsonify(current_state())
