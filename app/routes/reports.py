"""Management report routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import login_required
from ..db import get_session
from ..services.project_reports import (
    meeting_packet_report,
    portfolio_project_report,
    project_status_report,
)

bp = Blueprint("reports", __name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _bool_arg(name: str) -> bool:
    return (request.args.get(name) or "").strip().lower() in _TRUE_VALUES


def _is_admin() -> bool:
    return session.get("user_role") == "admin"


def _event_id_arg():
    raw_id = (request.args.get("event_id") or "").strip()
    if not raw_id:
        return None, "event_id is required"
    try:
        return int(raw_id), None
    except (TypeError, ValueError):
        return None, "event_id must be an integer"


def _project_args():
    project_number = (request.args.get("project_number") or "").strip()
    project_id = None
    raw_id = (request.args.get("project_id") or "").strip()
    if raw_id:
        try:
            project_id = int(raw_id)
        except (TypeError, ValueError):
            return None, "", "project_id must be an integer"
    if not project_id and not project_number:
        return None, "", "project_number or project_id is required"
    return project_id, project_number, None


def _portfolio_filters() -> dict:
    project_numbers = request.args.getlist("project_number")
    project_numbers.extend(request.args.getlist("project_numbers"))
    return {
        "q": (request.args.get("q") or "").strip(),
        "project_numbers": project_numbers,
        "client": (request.args.get("client") or "").strip(),
        "principal": (request.args.get("principal") or "").strip(),
        "component": (request.args.get("component") or "").strip(),
        "display_status": (request.args.get("display_status") or "").strip(),
        "include_inactive": _bool_arg("include_inactive"),
        "limit": request.args.get("limit"),
    }


@bp.route("/api/v1/reports/project", methods=["GET"])
@login_required
def project_report_json():
    project_id, project_number, error = _project_args()
    if error:
        return jsonify({"error": error}), 400
    report = project_status_report(
        get_session(),
        project_id=project_id,
        project_number=project_number,
        user_id=session.get("user_id"),
        include_private=_bool_arg("include_private"),
        is_admin=_is_admin(),
    )
    if report is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(report)


@bp.route("/reports/project", methods=["GET"])
@login_required
def project_report_page():
    project_id, project_number, error = _project_args()
    report = None
    if not error:
        report = project_status_report(
            get_session(),
            project_id=project_id,
            project_number=project_number,
            user_id=session.get("user_id"),
            include_private=_bool_arg("include_private"),
            is_admin=_is_admin(),
        )
        if report is None:
            error = "not found"
    return render_template(
        "project_report.html",
        report=report,
        error=error,
        project_number=project_number,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


@bp.route("/api/v1/reports/projects", methods=["GET"])
@login_required
def portfolio_report_json():
    packet = portfolio_project_report(
        get_session(),
        filters=_portfolio_filters(),
        user_id=session.get("user_id"),
        include_private=_bool_arg("include_private"),
        is_admin=_is_admin(),
    )
    return jsonify(packet)


@bp.route("/reports/projects", methods=["GET"])
@login_required
def portfolio_report_page():
    packet = portfolio_project_report(
        get_session(),
        filters=_portfolio_filters(),
        user_id=session.get("user_id"),
        include_private=_bool_arg("include_private"),
        is_admin=_is_admin(),
    )
    return render_template(
        "project_reports.html",
        packet=packet,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


@bp.route("/api/v1/reports/meeting", methods=["GET"])
@login_required
def meeting_report_json():
    event_id, error = _event_id_arg()
    if error:
        return jsonify({"error": error}), 400
    packet = meeting_packet_report(
        get_session(),
        event_id=event_id,
        user_id=session.get("user_id"),
        include_private=_bool_arg("include_private"),
        is_admin=_is_admin(),
    )
    if packet is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(packet)


@bp.route("/reports/meeting", methods=["GET"])
@login_required
def meeting_report_page():
    event_id, error = _event_id_arg()
    packet = None
    status = 200
    if error:
        status = 400
    else:
        packet = meeting_packet_report(
            get_session(),
            event_id=event_id,
            user_id=session.get("user_id"),
            include_private=_bool_arg("include_private"),
            is_admin=_is_admin(),
        )
        if packet is None:
            error = "not found"
            status = 404
    return render_template(
        "meeting_report.html",
        packet=packet,
        error=error,
        event_id=event_id,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    ), status
