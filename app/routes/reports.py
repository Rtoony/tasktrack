"""Management report routes."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from flask import Blueprint, Response, jsonify, render_template, request, session
from sqlalchemy import or_, select

from ..auth import admin_required, login_required
from ..db import get_session
from ..models import ReportPreset
from ..services.incident_reports import (
    INCIDENT_CSV_FIELDS,
    incident_csv_rows,
    incident_detail_report,
    incident_report,
)
from ..services.project_reports import (
    DEFAULT_PORTFOLIO_LIMIT,
    MAX_PORTFOLIO_LIMIT,
    meeting_packet_batch_report,
    meeting_packet_report,
    portfolio_project_report,
    project_status_report,
)

bp = Blueprint("reports", __name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_PRESET_SURFACES = {"portfolio", "incidents", "meetings"}
_TEXT_FILTER_KEYS = {"q", "client", "principal", "component", "display_status", "attention_level"}
_BOOL_FILTER_KEYS = {"include_inactive", "include_private"}
_LIST_FILTER_KEYS = {"project_numbers"}
_LIMIT_FILTER_KEYS = {"limit"}
_PORTFOLIO_FILTER_KEYS = _TEXT_FILTER_KEYS | _BOOL_FILTER_KEYS | _LIST_FILTER_KEYS | _LIMIT_FILTER_KEYS
_INCIDENT_TEXT_FILTER_KEYS = {"q", "severity", "status", "project_number", "person"}
_INCIDENT_BOOL_FILTER_KEYS = {"open_only", "follow_up_due"}
_INCIDENT_LIMIT_FILTER_KEYS = {"days", "limit"}
_INCIDENT_FILTER_KEYS = _INCIDENT_TEXT_FILTER_KEYS | _INCIDENT_BOOL_FILTER_KEYS | _INCIDENT_LIMIT_FILTER_KEYS
_MEETING_TEXT_FILTER_KEYS = {"project_number", "event_type"}
_MEETING_BOOL_FILTER_KEYS = {"include_private"}
_MEETING_LIMIT_FILTER_KEYS = {"days", "limit"}
_MEETING_FILTER_KEYS = _MEETING_TEXT_FILTER_KEYS | _MEETING_BOOL_FILTER_KEYS | _MEETING_LIMIT_FILTER_KEYS


def _bool_arg(name: str) -> bool:
    return (request.args.get(name) or "").strip().lower() in _TRUE_VALUES


def _is_admin() -> bool:
    return session.get("user_role") == "admin"


def _int_arg_value(raw, default: int, minimum: int, maximum: int) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _int_arg(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (request.args.get(name) or "").strip()
    return _int_arg_value(raw, default, minimum, maximum)


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


def _clean_project_numbers(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, (list, tuple)):
        raw_values = values
    else:
        raw_values = [str(values)]
    out: list[str] = []
    for value in raw_values:
        for part in str(value or "").replace("\n", ",").split(","):
            item = part.strip()
            if item and item not in out:
                out.append(item[:64])
    return out


def _clean_limit(raw):
    if raw in (None, ""):
        return DEFAULT_PORTFOLIO_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_PORTFOLIO_LIMIT
    return max(1, min(value, MAX_PORTFOLIO_LIMIT))


def _preset_keys(surface: str) -> set[str]:
    if surface == "incidents":
        return _INCIDENT_FILTER_KEYS
    if surface == "meetings":
        return _MEETING_FILTER_KEYS
    return _PORTFOLIO_FILTER_KEYS


def _clean_preset_filters(raw, *, surface: str = "portfolio") -> tuple[dict, str | None]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {}, "filters must be an object"
    unknown = sorted(set(raw) - _preset_keys(surface))
    if unknown:
        return {}, f"unsupported filter key: {unknown[0]}"

    filters: dict = {}
    if surface == "incidents":
        for key in _INCIDENT_TEXT_FILTER_KEYS:
            value = str(raw.get(key) or "").strip()
            if value:
                filters[key] = value[:160]
        for key in _INCIDENT_BOOL_FILTER_KEYS:
            if key in raw:
                filters[key] = bool(raw.get(key))
        if "days" in raw:
            filters["days"] = _int_arg_value(raw.get("days"), 365, 1, 3650)
        if "limit" in raw:
            filters["limit"] = _int_arg_value(raw.get("limit"), 100, 1, 250)
        return filters, None

    if surface == "meetings":
        for key in _MEETING_TEXT_FILTER_KEYS:
            value = str(raw.get(key) or "").strip()
            if value:
                filters[key] = value[:160]
        for key in _MEETING_BOOL_FILTER_KEYS:
            if key in raw:
                filters[key] = bool(raw.get(key))
        if "days" in raw:
            filters["days"] = _int_arg_value(raw.get("days"), 14, 1, 365)
        if "limit" in raw:
            filters["limit"] = _int_arg_value(raw.get("limit"), 12, 1, 25)
        return filters, None

    for key in _TEXT_FILTER_KEYS:
        value = str(raw.get(key) or "").strip()
        if value:
            filters[key] = value[:160]
    project_numbers = _clean_project_numbers(raw.get("project_numbers"))
    if project_numbers:
        filters["project_numbers"] = project_numbers
    for key in _BOOL_FILTER_KEYS:
        if key in raw:
            filters[key] = bool(raw.get(key))
    if "limit" in raw:
        filters["limit"] = _clean_limit(raw.get("limit"))
    return filters, None


def _request_portfolio_filters(*, only_present: bool = False) -> dict:
    filters: dict = {}
    for key in _TEXT_FILTER_KEYS:
        if not only_present or key in request.args:
            filters[key] = (request.args.get(key) or "").strip()
    if not only_present or "project_number" in request.args or "project_numbers" in request.args:
        project_numbers = request.args.getlist("project_number")
        project_numbers.extend(request.args.getlist("project_numbers"))
        filters["project_numbers"] = project_numbers
    for key in _BOOL_FILTER_KEYS:
        if not only_present or key in request.args:
            filters[key] = _bool_arg(key)
    if not only_present or "limit" in request.args:
        filters["limit"] = request.args.get("limit")
    cleaned, _ = _clean_preset_filters(filters, surface="portfolio")
    if "limit" not in filters and not only_present:
        cleaned["limit"] = None
    return cleaned


def _portfolio_filters() -> dict:
    filters = _request_portfolio_filters()
    filters.pop("include_private", None)
    return filters


def _request_incident_filters(*, only_present: bool = False) -> dict:
    filters: dict = {}
    for key in _INCIDENT_TEXT_FILTER_KEYS:
        if not only_present or key in request.args:
            filters[key] = (request.args.get(key) or "").strip()
    for key in _INCIDENT_BOOL_FILTER_KEYS:
        if not only_present or key in request.args:
            filters[key] = _bool_arg(key)
    if not only_present or "days" in request.args:
        filters["days"] = request.args.get("days")
    if not only_present or "limit" in request.args:
        filters["limit"] = request.args.get("limit")
    cleaned, _ = _clean_preset_filters(filters, surface="incidents")
    return cleaned


def _incident_filters() -> dict:
    return _request_incident_filters()


def _request_meeting_filters(*, only_present: bool = False) -> dict:
    filters: dict = {}
    for key in _MEETING_TEXT_FILTER_KEYS:
        if not only_present or key in request.args:
            filters[key] = (request.args.get(key) or "").strip()
    for key in _MEETING_BOOL_FILTER_KEYS:
        if not only_present or key in request.args:
            filters[key] = _bool_arg(key)
    if not only_present or "days" in request.args:
        filters["days"] = request.args.get("days")
    if not only_present or "limit" in request.args:
        filters["limit"] = request.args.get("limit")
    cleaned, _ = _clean_preset_filters(filters, surface="meetings")
    return cleaned


def _preset_filters(row: ReportPreset) -> dict:
    try:
        raw = json.loads(row.filters_json or "{}")
    except (TypeError, ValueError):
        raw = {}
    filters, _ = _clean_preset_filters(raw, surface=row.surface)
    return filters


def _preset_to_dict(row: ReportPreset, *, include_filters: bool = True) -> dict:
    out = {
        "id": row.id,
        "name": row.name,
        "surface": row.surface,
        "owner_user_id": row.owner_user_id,
        "is_shared": bool(row.is_shared),
        "created_at": str(row.created_at or ""),
        "updated_at": str(row.updated_at or ""),
    }
    if include_filters:
        out["filters"] = _preset_filters(row)
    return out


def _visible_presets_stmt(surface: str, *, user_id: int | None, is_admin: bool):
    stmt = select(ReportPreset).where(ReportPreset.surface == surface)
    if not is_admin:
        stmt = stmt.where(or_(ReportPreset.owner_user_id == user_id, ReportPreset.is_shared == 1))
    return stmt.order_by(ReportPreset.name.asc(), ReportPreset.id.asc())


def _preset_surface_allowed(surface: str, *, is_admin: bool) -> bool:
    return surface != "incidents" or is_admin


def _load_visible_preset(sess, preset_id: int, *, surface: str, user_id: int | None,
                         is_admin: bool) -> ReportPreset | None:
    row = sess.get(ReportPreset, preset_id)
    if row is None or row.surface != surface:
        return None
    if not _preset_surface_allowed(row.surface, is_admin=is_admin):
        return None
    if is_admin or row.is_shared or row.owner_user_id == user_id:
        return row
    return None


def _portfolio_context(sess):
    selected = None
    base_filters: dict = {}
    preset_id = (request.args.get("preset") or "").strip()
    if preset_id:
        try:
            preset_int = int(preset_id)
        except (TypeError, ValueError):
            return None, None, None, "preset must be an integer", 400
        selected = _load_visible_preset(
            sess, preset_int, surface="portfolio",
            user_id=session.get("user_id"), is_admin=_is_admin(),
        )
        if selected is None:
            return None, None, None, "preset not found", 404
        base_filters = _preset_filters(selected)

    explicit = _request_portfolio_filters(only_present=True)
    filters = {**base_filters, **{k: v for k, v in explicit.items() if k != "include_private"}}
    include_private = bool(base_filters.get("include_private"))
    if "include_private" in explicit:
        include_private = bool(explicit["include_private"])
    filters.pop("include_private", None)
    return filters, include_private, selected, None, 200


def _meeting_context(sess):
    selected = None
    base_filters: dict = {}
    preset_id = (request.args.get("preset") or "").strip()
    if preset_id:
        try:
            preset_int = int(preset_id)
        except (TypeError, ValueError):
            return None, None, "preset must be an integer", 400
        selected = _load_visible_preset(
            sess, preset_int, surface="meetings",
            user_id=session.get("user_id"), is_admin=_is_admin(),
        )
        if selected is None:
            return None, None, "preset not found", 404
        base_filters = _preset_filters(selected)

    explicit = _request_meeting_filters(only_present=True)
    filters = {**base_filters, **explicit}
    return filters, selected, None, 200


def _incident_context(sess):
    selected = None
    base_filters: dict = {}
    preset_id = (request.args.get("preset") or "").strip()
    if preset_id:
        try:
            preset_int = int(preset_id)
        except (TypeError, ValueError):
            return None, None, "preset must be an integer", 400
        selected = _load_visible_preset(
            sess, preset_int, surface="incidents",
            user_id=session.get("user_id"), is_admin=_is_admin(),
        )
        if selected is None:
            return None, None, "preset not found", 404
        base_filters = _preset_filters(selected)

    explicit = _request_incident_filters(only_present=True)
    filters = {**base_filters, **explicit}
    return filters, selected, None, 200


def _serialize_preset_payload(data: dict, *, user_id: int | None) -> tuple[dict, str | None]:
    name = str(data.get("name") or "").strip()
    if not name:
        return {}, "name is required"
    surface = str(data.get("surface") or "portfolio").strip()
    if surface not in _PRESET_SURFACES:
        return {}, "unsupported preset surface"
    filters, error = _clean_preset_filters(data.get("filters") or {}, surface=surface)
    if error:
        return {}, error
    return {
        "name": name[:80],
        "surface": surface,
        "filters_json": json.dumps(filters, sort_keys=True, separators=(",", ":")),
        "owner_user_id": user_id,
        "is_shared": 1 if data.get("is_shared") else 0,
    }, None


@bp.route("/reports", methods=["GET"])
@login_required
def reports_home():
    sess = get_session()
    is_admin = _is_admin()
    presets = sess.scalars(_visible_presets_stmt(
        "portfolio", user_id=session.get("user_id"), is_admin=is_admin,
    )).all()
    meeting_presets = sess.scalars(_visible_presets_stmt(
        "meetings", user_id=session.get("user_id"), is_admin=is_admin,
    )).all()
    incident_presets = []
    if is_admin:
        incident_presets = sess.scalars(_visible_presets_stmt(
            "incidents", user_id=session.get("user_id"), is_admin=True,
        )).all()
    return render_template(
        "reports_home.html",
        presets=[_preset_to_dict(row, include_filters=False) for row in presets],
        meeting_presets=[_preset_to_dict(row, include_filters=False) for row in meeting_presets],
        incident_presets=[_preset_to_dict(row, include_filters=False) for row in incident_presets],
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


def _today_brief_packet(sess):
    include_private = _bool_arg("include_private")
    is_admin = _is_admin()
    meeting_days = _int_arg("days", 1, 1, 14)
    meeting_limit = _int_arg("meeting_limit", 8, 1, 25)
    project_limit = _int_arg("project_limit", 8, 1, MAX_PORTFOLIO_LIMIT)
    meetings = meeting_packet_batch_report(
        sess,
        days=meeting_days,
        limit=meeting_limit,
        user_id=session.get("user_id"),
        include_private=include_private,
        is_admin=is_admin,
    )
    portfolio = portfolio_project_report(
        sess,
        filters={"attention_level": "at_risk", "limit": project_limit},
        user_id=session.get("user_id"),
        include_private=include_private,
        is_admin=is_admin,
    )
    incidents = incident_report(
        sess,
        filters={"open_only": True, "limit": 5, "days": 365},
    ) if is_admin else None
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": meeting_days,
        "include_private": include_private,
        "meetings": meetings,
        "at_risk": portfolio,
        "action_projects": portfolio.get("summary", {}).get("action_projects", []),
        "incidents": incidents,
    }


@bp.route("/api/v1/reports/today", methods=["GET"])
@login_required
def today_brief_json():
    return jsonify(_today_brief_packet(get_session()))


@bp.route("/reports/today", methods=["GET"])
@login_required
def today_brief_page():
    return render_template(
        "reports_today.html",
        packet=_today_brief_packet(get_session()),
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


@bp.route("/api/v1/reports/presets", methods=["GET"])
@login_required
def report_presets_list():
    surface = (request.args.get("surface") or "portfolio").strip()
    if surface not in _PRESET_SURFACES:
        return jsonify({"error": "unsupported preset surface"}), 400
    is_admin = _is_admin()
    if not _preset_surface_allowed(surface, is_admin=is_admin):
        return jsonify({"error": "forbidden"}), 403
    rows = get_session().scalars(_visible_presets_stmt(
        surface, user_id=session.get("user_id"), is_admin=is_admin,
    )).all()
    return jsonify({"presets": [_preset_to_dict(row) for row in rows]})


@bp.route("/api/v1/reports/presets", methods=["POST"])
@login_required
def report_presets_create():
    data = request.get_json(silent=True) or {}
    requested_surface = str(data.get("surface") or "portfolio").strip()
    if not _preset_surface_allowed(requested_surface, is_admin=_is_admin()):
        return jsonify({"error": "forbidden"}), 403
    payload, error = _serialize_preset_payload(data, user_id=session.get("user_id"))
    if error:
        return jsonify({"error": error}), 400
    row = ReportPreset(**payload)
    sess = get_session()
    sess.add(row)
    sess.commit()
    return jsonify(_preset_to_dict(row)), 201


def _can_modify_preset(row: ReportPreset) -> bool:
    return bool(_is_admin() or row.owner_user_id == session.get("user_id"))


@bp.route("/api/v1/reports/presets/<int:preset_id>", methods=["PUT", "PATCH"])
@login_required
def report_presets_update(preset_id: int):
    sess = get_session()
    row = sess.get(ReportPreset, preset_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    requested_surface = str(data.get("surface") or row.surface or "portfolio").strip()
    is_admin = _is_admin()
    if not _can_modify_preset(row) or not _preset_surface_allowed(row.surface, is_admin=is_admin) or not _preset_surface_allowed(requested_surface, is_admin=is_admin):
        return jsonify({"error": "forbidden"}), 403
    payload, error = _serialize_preset_payload(data, user_id=row.owner_user_id)
    if error:
        return jsonify({"error": error}), 400
    row.name = payload["name"]
    row.surface = payload["surface"]
    row.filters_json = payload["filters_json"]
    row.is_shared = payload["is_shared"]
    row.updated_at = datetime.now()
    sess.commit()
    return jsonify(_preset_to_dict(row))


@bp.route("/api/v1/reports/presets/<int:preset_id>", methods=["DELETE"])
@login_required
def report_presets_delete(preset_id: int):
    sess = get_session()
    row = sess.get(ReportPreset, preset_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    if not _can_modify_preset(row) or not _preset_surface_allowed(row.surface, is_admin=_is_admin()):
        return jsonify({"error": "forbidden"}), 403
    sess.delete(row)
    sess.commit()
    return jsonify({"ok": True})


@bp.route("/api/v1/reports/incidents", methods=["GET"])
@admin_required
def incident_report_json():
    sess = get_session()
    filters, selected, error, status = _incident_context(sess)
    if error:
        return jsonify({"error": error}), status
    packet = incident_report(sess, filters=filters)
    packet["selected_preset"] = _preset_to_dict(selected, include_filters=False) if selected else None
    return jsonify(packet)


@bp.route("/api/v1/reports/incidents.csv", methods=["GET"])
@admin_required
def incident_report_csv():
    sess = get_session()
    filters, selected, error, status = _incident_context(sess)
    if error:
        return jsonify({"error": error}), status
    packet = incident_report(sess, filters=filters)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=INCIDENT_CSV_FIELDS)
    writer.writeheader()
    writer.writerows(incident_csv_rows(packet))
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=incident_report_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


@bp.route("/reports/incidents", methods=["GET"])
@admin_required
def incident_report_page():
    sess = get_session()
    filters, selected, error, status = _incident_context(sess)
    if error:
        filters = _incident_filters()
    packet = incident_report(sess, filters=filters)
    packet["selected_preset"] = _preset_to_dict(selected, include_filters=False) if selected else None
    presets = sess.scalars(_visible_presets_stmt(
        "incidents", user_id=session.get("user_id"), is_admin=True,
    )).all()
    return render_template(
        "incident_reports.html",
        packet=packet,
        presets=[_preset_to_dict(row, include_filters=False) for row in presets],
        error=error,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    ), status


@bp.route("/api/v1/reports/incidents/<int:incident_id>", methods=["GET"])
@admin_required
def incident_detail_report_json(incident_id: int):
    packet = incident_detail_report(get_session(), incident_id=incident_id)
    if packet is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(packet)


@bp.route("/reports/incidents/<int:incident_id>", methods=["GET"])
@admin_required
def incident_detail_report_page(incident_id: int):
    packet = incident_detail_report(get_session(), incident_id=incident_id)
    status = 200 if packet is not None else 404
    return render_template(
        "incident_report.html",
        packet=packet,
        error=None if packet is not None else "not found",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    ), status


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
    sess = get_session()
    filters, include_private, selected, error, status = _portfolio_context(sess)
    if error:
        return jsonify({"error": error}), status
    packet = portfolio_project_report(
        sess,
        filters=filters,
        user_id=session.get("user_id"),
        include_private=include_private,
        is_admin=_is_admin(),
    )
    packet["selected_preset"] = _preset_to_dict(selected, include_filters=False) if selected else None
    return jsonify(packet)


@bp.route("/api/v1/reports/projects/actions.csv", methods=["GET"])
@login_required
def portfolio_actions_csv():
    sess = get_session()
    filters, include_private, selected, error, status = _portfolio_context(sess)
    if error:
        return jsonify({"error": error}), status
    packet = portfolio_project_report(
        sess,
        filters=filters,
        user_id=session.get("user_id"),
        include_private=include_private,
        is_admin=_is_admin(),
    )
    fields = [
        "project_number", "name", "client", "attention_level", "primary_action",
        "primary_action_detail", "headline", "overdue_count", "open_count",
        "next_due", "project_report_url", "workspace_url", "map_url",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in packet.get("summary", {}).get("action_projects", []):
        project_number = row.get("project_number") or ""
        payload = {key: row.get(key, "") for key in fields}
        payload["project_report_url"] = f"/reports/project?project_number={project_number}"
        payload["workspace_url"] = f"/?workspace={project_number}"
        payload["map_url"] = f"/?map_project={project_number}"
        writer.writerow(payload)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=portfolio_action_queue_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


@bp.route("/reports/projects", methods=["GET"])
@login_required
def portfolio_report_page():
    sess = get_session()
    filters, include_private, selected, error, status = _portfolio_context(sess)
    if error:
        filters = _portfolio_filters()
        include_private = _bool_arg("include_private")
    packet = portfolio_project_report(
        sess,
        filters=filters,
        user_id=session.get("user_id"),
        include_private=include_private,
        is_admin=_is_admin(),
    )
    packet["selected_preset"] = _preset_to_dict(selected, include_filters=False) if selected else None
    presets = sess.scalars(_visible_presets_stmt(
        "portfolio", user_id=session.get("user_id"), is_admin=_is_admin(),
    )).all()
    return render_template(
        "project_reports.html",
        packet=packet,
        presets=[_preset_to_dict(row, include_filters=False) for row in presets],
        error=error,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    ), status


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


@bp.route("/api/v1/reports/meetings", methods=["GET"])
@login_required
def meeting_batch_report_json():
    sess = get_session()
    filters, selected, error, status = _meeting_context(sess)
    if error:
        return jsonify({"error": error}), status
    packet = meeting_packet_batch_report(
        sess,
        days=filters.get("days", 14),
        limit=filters.get("limit", 12),
        project_number=filters.get("project_number", ""),
        event_type=filters.get("event_type", ""),
        user_id=session.get("user_id"),
        include_private=bool(filters.get("include_private")),
        is_admin=_is_admin(),
    )
    packet["selected_preset"] = _preset_to_dict(selected, include_filters=False) if selected else None
    return jsonify(packet)


@bp.route("/reports/meetings", methods=["GET"])
@login_required
def meeting_batch_report_page():
    sess = get_session()
    filters, selected, error, status = _meeting_context(sess)
    if error:
        filters = _request_meeting_filters()
    packet = meeting_packet_batch_report(
        sess,
        days=filters.get("days", 14),
        limit=filters.get("limit", 12),
        project_number=filters.get("project_number", ""),
        event_type=filters.get("event_type", ""),
        user_id=session.get("user_id"),
        include_private=bool(filters.get("include_private")),
        is_admin=_is_admin(),
    )
    packet["selected_preset"] = _preset_to_dict(selected, include_filters=False) if selected else None
    presets = sess.scalars(_visible_presets_stmt(
        "meetings", user_id=session.get("user_id"), is_admin=_is_admin(),
    )).all()
    return render_template(
        "meeting_reports.html",
        packet=packet,
        presets=[_preset_to_dict(row, include_filters=False) for row in presets],
        error=error,
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    ), status


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
