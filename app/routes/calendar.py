"""Calendar widget route — read-only Radicale glance."""
from flask import Blueprint, jsonify, request

from ..services.calendar import calendar_upcoming_events

bp = Blueprint("calendar", __name__)


@bp.route("/api/calendar/upcoming", methods=["GET"])
def calendar_upcoming():
    days = request.args.get("days", default=30, type=int)
    limit = request.args.get("limit", default=8, type=int)
    days = max(1, min(days, 365))
    limit = max(1, min(limit, 50))
    return jsonify(calendar_upcoming_events(days=days, limit=limit))
