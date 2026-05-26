"""Internal calendar agenda endpoints."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from flask import Blueprint, jsonify, request, session
from sqlalchemy import select

from ..auth import login_required
from ..db import get_session
from ..models import CalendarEvent

bp = Blueprint("calendar", __name__)

_DONE_STATUSES = {"done", "cancelled"}


def _clamp_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _parse_iso_datetime(raw: str | None) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_window_start(raw: str | None, fallback: datetime) -> datetime:
    parsed = _parse_iso_datetime(raw)
    return parsed if parsed is not None else fallback


def _parse_window_end(raw: str | None, fallback: datetime) -> datetime:
    parsed = _parse_iso_datetime(raw)
    if parsed is None:
        return fallback
    if raw and len(raw.strip()) == 10:
        return datetime.combine(parsed.date(), time.max)
    return parsed


def _event_start(row: CalendarEvent) -> datetime | None:
    return _parse_iso_datetime(row.start_at)


def _visible_to_current_user(row: CalendarEvent) -> bool:
    if row.visibility != "private":
        return True
    return row.created_by_user_id == session.get("user_id")


def _serialize(row: CalendarEvent) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description or "",
        "event_type": row.event_type,
        "type": row.event_type,
        "status": row.status,
        "start": row.start_at,
        "start_at": row.start_at,
        "end": row.end_at or "",
        "end_at": row.end_at or "",
        "all_day": bool(row.all_day),
        "location": row.location or "",
        "project_id": row.project_id,
        "project_number": row.project_number or "",
        "related_table": row.related_table or "",
        "related_id": row.related_id,
        "reminder_date": row.reminder_date or "",
        "visibility": row.visibility,
    }


def _base_rows() -> list[CalendarEvent]:
    sess = get_session()
    rows = sess.scalars(select(CalendarEvent).order_by(CalendarEvent.start_at.asc())).all()
    return [row for row in rows if _visible_to_current_user(row)]


@bp.route("/api/v1/calendar/upcoming")
@login_required
def upcoming_events():
    days = _clamp_int(request.args.get("days"), 30, 1, 365)
    limit = _clamp_int(request.args.get("limit"), 10, 1, 50)
    now = datetime.now()
    end = now + timedelta(days=days)

    events: list[tuple[datetime, CalendarEvent]] = []
    for row in _base_rows():
        if row.status in _DONE_STATUSES:
            continue
        start = _event_start(row)
        if start is None:
            continue
        if row.all_day:
            if now.date() <= start.date() <= end.date():
                events.append((start, row))
        elif now <= start <= end:
            events.append((start, row))
    events.sort(key=lambda item: item[0])
    return jsonify({
        "available": True,
        "events": [_serialize(row) for _, row in events[:limit]],
    })


@bp.route("/api/v1/calendar/events")
@login_required
def range_events():
    now = datetime.now()
    window_start = _parse_window_start(request.args.get("from"), now - timedelta(days=30))
    window_end = _parse_window_end(request.args.get("to"), now + timedelta(days=365))
    if window_end < window_start:
        return jsonify({"error": "to must be after from"}), 400

    project_id = request.args.get("project_id")
    event_type = (request.args.get("type") or request.args.get("event_type") or "").strip()
    status = (request.args.get("status") or "").strip()

    rows = []
    for row in _base_rows():
        start = _event_start(row)
        if start is None or not (window_start <= start <= window_end):
            continue
        if project_id and str(row.project_id or "") != project_id:
            continue
        if event_type and row.event_type != event_type:
            continue
        if status and row.status != status:
            continue
        rows.append((start, row))
    rows.sort(key=lambda item: item[0])
    return jsonify([_serialize(row) for _, row in rows])
