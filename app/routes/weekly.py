"""Weekly aggregation route (Phase 6).

- GET /api/v1/weekly?days=7              — JSON snapshot
- GET /api/v1/weekly?days=7&breakdown=1  — JSON snapshot + status rollups
- GET /weekly?days=7                     — HTML render
- GET /weekly?days=7&breakdown=1         — HTML render + status rollups

Both gates behind @login_required. The HTML view honours admin role to
decide whether to include the skill_score_changes block; the JSON
endpoint follows the same rule. `days` clamps to [1, 90]. `breakdown`
adds per-tracker by_status / by_assignee / age_buckets counts.
"""
from flask import Blueprint, jsonify, render_template, request, session

from ..auth import login_required
from ..db import get_session
from ..routes.reports import _active_report_section, _visible_report_sections
from ..services.weekly import weekly_snapshot

bp = Blueprint("weekly", __name__)


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


@bp.route("/api/v1/weekly", methods=["GET"])
@login_required
def weekly_json():
    sess = get_session()
    return jsonify(weekly_snapshot(
        sess, days=_days_arg(), include_admin=_is_admin(),
        user_id=session.get("user_id"), breakdown=_breakdown_arg(),
    ))


@bp.route("/weekly", methods=["GET"])
@login_required
def weekly_page():
    sess = get_session()
    show_breakdown = _breakdown_arg()
    snapshot = weekly_snapshot(
        sess, days=_days_arg(), include_admin=_is_admin(),
        user_id=session.get("user_id"), breakdown=show_breakdown,
    )
    return render_template(
        "weekly.html",
        snapshot=snapshot,
        show_breakdown=show_breakdown,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        is_admin=_is_admin(),
        **_weekly_report_nav(),
    )
