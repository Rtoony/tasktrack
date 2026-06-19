"""Weekly aggregation route (Phase 6).

- GET /api/v1/weekly?days=7              — JSON snapshot
- GET /api/v1/weekly?days=7&breakdown=1  — JSON snapshot + status rollups
- GET /api/v1/weekly?week_offset=1       — last full calendar week (Mon-Sun)
- GET /weekly?days=7                     — HTML render
- GET /weekly?days=7&breakdown=1         — HTML render + status rollups
- GET /weekly?week_offset=1              — HTML render of last calendar week

Both gates behind @login_required. The HTML view honours admin role to
decide whether to include the skill_score_changes block; the JSON
endpoint follows the same rule. `days` clamps to [1, 90]. `breakdown`
adds per-tracker by_status / by_assignee / age_buckets counts.

W8 — `week_offset`: 0 (default) = the live trailing `days`-day window,
byte-for-byte the original behavior. >=1 anchors a *past* full calendar
week (Mon 00:00 .. next-Mon 00:00 UTC): 1 = last week, 2 = the week
before, etc., passing an explicit since+until so a closed week reads the
same on Monday as on Friday.
"""
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import login_required
from ..db import get_session
from ..routes.reports import _active_report_section, _visible_report_sections
from ..services.weekly import weekly_snapshot

bp = Blueprint("weekly", __name__)

# Cap how far back the picker can reach so a hand-typed week_offset can't
# walk the snapshot off into empty pre-history (and bloat nothing).
MAX_WEEK_OFFSET = 520  # ~10 years of weeks


def _weekly_report_nav() -> dict:
    """Mirror reports.report_nav_context() for the weekly page.

    /weekly lives in this blueprint, so the reports blueprint's
    context-processor never reaches it. We import the canonical helpers and
    build the same nav payload here so Week in Review renders inside the
    shared report shell with the correct active section highlighted.
    """
    active = _active_report_section()
    sections = _visible_report_sections()
    active_meta = next(
        (section for section in sections if section["key"] == active),
        sections[0],
    )
    return {
        "report_sections": sections,
        "active_report_section": active,
        "active_report_meta": active_meta,
    }


def _days_arg() -> int:
    raw = request.args.get("days", default="7")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 7
    return max(1, min(v, 90))


def _is_admin() -> bool:
    return session.get("user_role") == "admin"


def _breakdown_arg() -> bool:
    """Opt-in status/assignee/age rollups via ?breakdown=1 (also accepts
    true/yes/on). Defaults off to keep the standard payload lean."""
    raw = (request.args.get("breakdown", default="") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _week_offset_arg() -> int:
    """W8: how many full calendar weeks back to anchor on. 0 (default, and
    any garbage/negative input) = the live trailing window. Clamped to
    [0, MAX_WEEK_OFFSET]."""
    raw = request.args.get("week_offset", default="0")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 0
    return max(0, min(v, MAX_WEEK_OFFSET))


def _calendar_week_bounds(week_offset: int) -> tuple[datetime, datetime]:
    """W8: UTC [since, until) bounds for the calendar week `week_offset`
    weeks back. offset 1 = the last *completed* Mon 00:00 .. next-Mon 00:00
    week; 2 = the week before that, etc.

    Anchored on this week's Monday 00:00 UTC, then stepped back
    `week_offset` weeks. `until` is the following Monday 00:00 — i.e. the
    end of the chosen week — so the window is exactly one Mon-Sun span and
    never includes the live, still-accruing current week.
    """
    now = datetime.now(tz=UTC)
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    since = this_monday - timedelta(weeks=week_offset)
    until = since + timedelta(weeks=1)
    return since, until


def _window_kwargs() -> dict:
    """Resolve the (since, until, days) the snapshot should use from the
    request: a bounded past calendar week when week_offset>=1, else the
    default trailing window (since/until left to weekly_snapshot, days from
    the `days` arg). Centralised so the JSON and HTML routes can't drift."""
    week_offset = _week_offset_arg()
    if week_offset >= 1:
        since, until = _calendar_week_bounds(week_offset)
        return {"since": since, "until": until, "days": (until - since).days}
    return {"days": _days_arg()}


@bp.route("/api/v1/weekly", methods=["GET"])
@login_required
def weekly_json():
    sess = get_session()
    return jsonify(weekly_snapshot(
        sess, include_admin=_is_admin(),
        user_id=session.get("user_id"), breakdown=_breakdown_arg(),
        **_window_kwargs(),
    ))


@bp.route("/weekly", methods=["GET"])
@login_required
def weekly_page():
    sess = get_session()
    show_breakdown = _breakdown_arg()
    week_offset = _week_offset_arg()
    snapshot = weekly_snapshot(
        sess, include_admin=_is_admin(),
        user_id=session.get("user_id"), breakdown=show_breakdown,
        **_window_kwargs(),
    )
    return render_template(
        "weekly.html",
        snapshot=snapshot,
        show_breakdown=show_breakdown,
        week_offset=week_offset,
        max_week_offset=MAX_WEEK_OFFSET,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        is_admin=_is_admin(),
        **_weekly_report_nav(),
    )
