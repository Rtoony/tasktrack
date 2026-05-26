"""Management report routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import login_required
from ..db import get_session
from ..services.project_reports import project_status_report

bp = Blueprint("reports", __name__)


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
