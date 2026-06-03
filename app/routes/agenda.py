"""Bot-scoped unified agenda — TaskTrack calendar_events + Radicale CalDAV.

A read-only merged calendar feed for headless callers (the Hermes assistant),
token-authenticated with the `bot` scope. Combines two internal sources that
both live on this host:

  1. TaskTrack's own `calendar_events` table.
  2. Radicale CalDAV collections (the "Nexus Portal calendar") read straight
     from the .ics files on disk — same pattern the portal uses, no CalDAV
     round-trip, no extra dependency.

    GET /api/v1/agenda?days=7

Read-only. Never writes. Google is intentionally NOT a source.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from .. import limiter
from ..db import get_session
from ..models import CalendarEvent
from ..tokens import check_scoped_token

bp = Blueprint("agenda", __name__)

RADICALE_ROOT = Path(os.environ.get(
    "RADICALE_COLLECTIONS_ROOT", str(Path.home() / ".var/lib/radicale/collections")
))
RADICALE_USER = os.environ.get("RADICALE_USER_DIR", "rtoony")
_DEFAULT_DAYS = 7
_DONE = {"cancelled", "done"}


def _skip_limit_for_tests() -> bool:
    return bool(current_app.config.get("TESTING"))


def _clamp(raw, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(raw), hi))
    except (TypeError, ValueError):
        return default


def _parse_iso(raw):
    """Parse a TaskTrack start_at ('2026-06-02T09:00' / with seconds / date)."""
    if not raw:
        return None
    val = str(raw).strip().replace("Z", "")
    try:
        return datetime.fromisoformat(val)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _parse_ics_dt(raw: str):
    """Parse an iCalendar DTSTART value → (naive datetime, all_day bool) or (None, None).

    Handles '20260512T090000Z' (UTC), '20260512T090000' (floating/TZID),
    and '20260512' (VALUE=DATE all-day). UTC 'Z' times are treated as the
    wall time for day-window filtering — good enough for an agenda.
    """
    m = re.search(r"(\d{8})(?:T(\d{6}))?", raw.strip())
    if not m:
        return None, None
    day, tod = m.group(1), m.group(2)
    try:
        if tod:
            return datetime.strptime(day + tod, "%Y%m%d%H%M%S"), False
        return datetime.strptime(day, "%Y%m%d"), True
    except ValueError:
        return None, None


def _read_radicale(window_start: datetime, window_end: datetime) -> list[dict]:
    events: list[dict] = []
    base = RADICALE_ROOT / "collection-root" / RADICALE_USER
    if not base.exists():
        return events
    for coll_dir in sorted(base.iterdir()):
        if not coll_dir.is_dir():
            continue
        coll = coll_dir.name
        for f in coll_dir.glob("*.ics"):
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            summary = location = None
            start = None
            all_day = False
            for line in text.splitlines():
                if line.startswith("SUMMARY"):
                    summary = line.split(":", 1)[-1].strip()
                elif line.startswith("DTSTART"):
                    start, all_day = _parse_ics_dt(line.split(":", 1)[-1])
                elif line.startswith("LOCATION"):
                    location = line.split(":", 1)[-1].strip()
            if start is None or not (window_start <= start <= window_end):
                continue
            events.append({
                "title": summary or "(untitled)",
                "start": start.isoformat(),
                "all_day": all_day,
                "location": location or "",
                "source": f"radicale:{coll}",
            })
    return events


def _read_tasktrack(sess, window_start: datetime, window_end: datetime) -> list[dict]:
    events: list[dict] = []
    for row in sess.scalars(select(CalendarEvent)).all():
        if (row.status or "").lower() in _DONE:
            continue
        if getattr(row, "visibility", "") == "private":
            continue
        start = _parse_iso(row.start_at)
        if start is None or not (window_start <= start <= window_end):
            continue
        events.append({
            "title": row.title or "(untitled)",
            "start": start.isoformat(),
            "all_day": bool(getattr(row, "all_day", 0)),
            "location": row.location or "",
            "event_type": row.event_type or "",
            "project_number": getattr(row, "project_number", "") or "",
            "source": "tasktrack",
        })
    return events


@bp.route("/api/v1/agenda", methods=["GET"])
@limiter.limit("60 per minute; 600 per hour", exempt_when=_skip_limit_for_tests)
def agenda():
    err = check_scoped_token("bot")
    if err:
        return err

    days = _clamp(request.args.get("days"), _DEFAULT_DAYS, 1, 90)
    now = datetime.now()
    window_start = datetime.combine(date.today(), datetime.min.time())
    window_end = now + timedelta(days=days)

    sess = get_session()
    events = _read_tasktrack(sess, window_start, window_end) + \
        _read_radicale(window_start, window_end)
    events.sort(key=lambda e: e["start"])

    by_source: dict[str, int] = {}
    for e in events:
        key = e["source"].split(":", 1)[0]
        by_source[key] = by_source.get(key, 0) + 1

    return jsonify({
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "window": {"days": days, "from": window_start.isoformat(),
                   "to": window_end.isoformat()},
        "counts": {"total": len(events), "by_source": by_source},
        "events": events,
    })
