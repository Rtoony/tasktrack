"""Read-only calendar widget — reads Radicale .ics files from disk.

Reads ~/.var/lib/radicale/collections by default; override with
RADICALE_COLLECTIONS_ROOT.
"""
import os
from pathlib import Path

_RADICALE_ROOT = Path(
    os.environ.get(
        "RADICALE_COLLECTIONS_ROOT",
        str(Path.home() / ".var/lib/radicale/collections"),
    )
)
_RADICALE_USER_DIR = "rtoony"


def _calendar_is_date_only(value):
    from datetime import date as _date, datetime as _datetime
    return isinstance(value, _date) and not isinstance(value, _datetime)


def calendar_upcoming_events(days: int = 30, limit: int = 8):
    try:
        from icalendar import Calendar as _ICalendar
    except ImportError:
        return {"available": False, "reason": "icalendar not installed", "events": []}

    user_dir = _RADICALE_ROOT / "collection-root" / _RADICALE_USER_DIR
    if not user_dir.is_dir():
        return {"available": False, "reason": "Radicale collections not found", "events": []}

    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now = _dt.now(tz=_tz.utc)
    horizon = now + _td(days=days)
    out = []

    for collection_dir in user_dir.iterdir():
        if not collection_dir.is_dir():
            continue
        for ics_file in collection_dir.glob("*.ics"):
            try:
                cal = _ICalendar.from_ical(ics_file.read_bytes())
            except Exception:
                continue
            for component in cal.walk("VEVENT"):
                dtstart = component.get("DTSTART")
                if dtstart is None:
                    continue
                start = dtstart.dt
                dtend = component.get("DTEND")
                end = dtend.dt if dtend is not None else start

                all_day = _calendar_is_date_only(start) or _calendar_is_date_only(end)
                if all_day:
                    cmp_start = _dt(start.year, start.month, start.day, tzinfo=_tz.utc)
                    e_end = end if hasattr(end, "year") else start
                    cmp_end = _dt(e_end.year, e_end.month, e_end.day, tzinfo=_tz.utc)
                else:
                    cmp_start = start if getattr(start, "tzinfo", None) else start.replace(tzinfo=_tz.utc)
                    cmp_end = end if getattr(end, "tzinfo", None) else end.replace(tzinfo=_tz.utc)

                if cmp_end < now or cmp_start > horizon:
                    continue

                if all_day:
                    display_start = f"{start.year:04d}-{start.month:02d}-{start.day:02d}"
                else:
                    display_start = cmp_start.isoformat()

                out.append({
                    "id": str(component.get("UID", ics_file.stem)),
                    "title": str(component.get("SUMMARY", "(untitled)")),
                    "collection": collection_dir.name,
                    "start": display_start,
                    "all_day": all_day,
                    "location": str(component.get("LOCATION")) if component.get("LOCATION") else None,
                })

    out.sort(key=lambda e: e["start"])
    return {
        "available": True,
        "events": out[:limit],
        "collections_scanned": [d.name for d in user_dir.iterdir() if d.is_dir()],
        "server_time": now.isoformat(),
    }
