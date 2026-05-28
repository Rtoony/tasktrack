"""Management report routes."""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy import or_, select

from ..auth import login_required
from ..db import get_session
from ..models import ReportPreset
from ..services.project_reports import (
    DEFAULT_PORTFOLIO_LIMIT,
    MAX_PORTFOLIO_LIMIT,
    meeting_packet_report,
    portfolio_project_report,
    project_status_report,
)

bp = Blueprint("reports", __name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_PRESET_SURFACES = {"portfolio"}
_TEXT_FILTER_KEYS = {"q", "client", "principal", "component", "display_status"}
_BOOL_FILTER_KEYS = {"include_inactive", "include_private"}
_LIST_FILTER_KEYS = {"project_numbers"}
_LIMIT_FILTER_KEYS = {"limit"}
_PORTFOLIO_FILTER_KEYS = _TEXT_FILTER_KEYS | _BOOL_FILTER_KEYS | _LIST_FILTER_KEYS | _LIMIT_FILTER_KEYS


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


def _clean_preset_filters(raw) -> tuple[dict, str | None]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {}, "filters must be an object"
    unknown = sorted(set(raw) - _PORTFOLIO_FILTER_KEYS)
    if unknown:
        return {}, f"unsupported filter key: {unknown[0]}"

    filters: dict = {}
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
    cleaned, _ = _clean_preset_filters(filters)
    if "limit" not in filters and not only_present:
        cleaned["limit"] = None
    return cleaned


def _portfolio_filters() -> dict:
    filters = _request_portfolio_filters()
    filters.pop("include_private", None)
    return filters


def _preset_filters(row: ReportPreset) -> dict:
    try:
        raw = json.loads(row.filters_json or "{}")
    except (TypeError, ValueError):
        raw = {}
    filters, _ = _clean_preset_filters(raw)
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


def _load_visible_preset(sess, preset_id: int, *, surface: str, user_id: int | None,
                         is_admin: bool) -> ReportPreset | None:
    row = sess.get(ReportPreset, preset_id)
    if row is None or row.surface != surface:
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


def _serialize_preset_payload(data: dict, *, user_id: int | None) -> tuple[dict, str | None]:
    name = str(data.get("name") or "").strip()
    if not name:
        return {}, "name is required"
    surface = str(data.get("surface") or "portfolio").strip()
    if surface not in _PRESET_SURFACES:
        return {}, "unsupported preset surface"
    filters, error = _clean_preset_filters(data.get("filters") or {})
    if error:
        return {}, error
    return {
        "name": name[:80],
        "surface": surface,
        "filters_json": json.dumps(filters, sort_keys=True, separators=(",", ":")),
        "owner_user_id": user_id,
        "is_shared": 1 if data.get("is_shared") else 0,
    }, None


@bp.route("/api/v1/reports/presets", methods=["GET"])
@login_required
def report_presets_list():
    surface = (request.args.get("surface") or "portfolio").strip()
    if surface not in _PRESET_SURFACES:
        return jsonify({"error": "unsupported preset surface"}), 400
    rows = get_session().scalars(_visible_presets_stmt(
        surface, user_id=session.get("user_id"), is_admin=_is_admin(),
    )).all()
    return jsonify({"presets": [_preset_to_dict(row) for row in rows]})


@bp.route("/api/v1/reports/presets", methods=["POST"])
@login_required
def report_presets_create():
    data = request.get_json(silent=True) or {}
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
    if not _can_modify_preset(row):
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
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
    if not _can_modify_preset(row):
        return jsonify({"error": "forbidden"}), 403
    sess.delete(row)
    sess.commit()
    return jsonify({"ok": True})


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
