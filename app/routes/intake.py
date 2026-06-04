"""Internal request intake forms.

Routes live at /intake/* (renamed from /submit/*). The legacy /submit/*
paths still resolve via a 308 redirect installed in app/__init__.py.

Capability submissions are not part of the intake surface — they land
via the authenticated dashboard only.

Per-route rate limits run off INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP
(default 60/hr per IP). Limits apply only on POST submissions; GETs
stay browseable.
"""
import json
from datetime import date
from functools import wraps

from sqlalchemy import or_, select

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

from .. import limiter
from .. import profile as _profile
from ..auth import login_required
from ..csrf import get_csrf_token
from ..db import get_session
from ..models import InboxItem, Project
from ..services.audit import log_activity
from ..services.ocr_forms import (
    PRINTABLE_REQUEST_FORMS,
    parse_printable_form_ocr,
    printable_form_record_payload,
)
from ..services.tickets import create_direct_record

bp = Blueprint("intake", __name__)


def _intake_post_limit():
    return f"{_profile.INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP} per hour"


def intake_auth_required(f):
    """No-op decorator — intake is open. Kept as a seam in case this
    private install ever fronts intake URLs publicly and access needs to flip."""

    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


_TYPE_META = {
    "general": {
        "label": "General request",
        "promote": None,
        "route": "Routed to the right team",
        "required": ("summary", "details"),
    },
    "project_work": {
        "label": "Project work",
        "promote": "project_work_tasks",
        "route": "Project work queue",
        "required": ("project", "summary"),
    },
    "cad": {
        "label": "CAD / Drafting",
        "promote": "work_tasks",
        "route": "CAD / Drafting team",
        "required": ("summary",),
    },
    "training": {
        "label": "Training",
        "promote": "training_tasks",
        "route": "Training coordinator",
        "required": ("topic", "goals"),
    },
    "suggestion": {
        "label": "Suggestion / Idea",
        "promote": "personal_items",
        "route": "Suggestion box",
        "required": ("title", "body"),
    },
    "problem": {
        "label": "Report a problem",
        "promote": "personnel_issues",
        "route": "Reviewed confidentially",
        "required": ("details",),
    },
}
_VALID_PRIORITIES = {"Low", "Medium", "High"}
_VALID_SEVERITIES = {"Low", "Medium", "High", "Critical"}


def _clean_fields(raw_fields) -> dict:
    if not isinstance(raw_fields, dict):
        return {}
    cleaned = {}
    for key, value in raw_fields.items():
        if isinstance(value, str):
            cleaned[str(key)] = value.strip()
        elif value is not None:
            cleaned[str(key)] = value
    return cleaned


def _validate_iso_date(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("desired_by must be YYYY-MM-DD")
    return value


def _request_title(rtype: str, fields: dict) -> str:
    if rtype == "problem":
        details = str(fields.get("details") or "").strip()
        return (details.splitlines()[0] if details else "Problem report")[:160]
    return str(
        fields.get("summary")
        or fields.get("title")
        or fields.get("topic")
        or fields.get("details")
        or "Request"
    ).strip()[:160]


def _title_and_body(
    rtype: str, fields: dict, priority: str, severity: str, desired_by: str
) -> tuple[str, str]:
    meta = _TYPE_META[rtype]
    title = _request_title(rtype, fields)
    lines = [f"Request type: {meta['label']}"]
    for key in (
        "project",
        "phase",
        "scheduled_completion_at",
        "time_required_minutes",
        "skill",
        "software",
        "who",
        "trainees",
        "category",
        "goals",
        "details",
        "body",
        "involved",
    ):
        value = fields.get(key)
        if value:
            lines.append(f"{key}: {value}")
    if rtype == "problem" and severity:
        lines.append(f"severity: {severity}")
    elif priority:
        lines.append(f"priority: {priority}")
    if desired_by:
        lines.append(f"needed_by: {desired_by}")
    meta_block = {
        "type": rtype,
        "suggested_target": meta.get("promote") or "triage",
        "fields": fields,
    }
    lines.append("INTAKE_META: " + json.dumps(meta_block, sort_keys=True))
    return title, "\n".join(lines)


def _redirect_to_request(rtype: str):
    return redirect(f"/intake/request?type={rtype}", code=302)


@bp.route("/intake/request")
@login_required
def br_intake_request():
    user_name = session.get("user_name", "")
    initials = (
        "".join(part[0] for part in user_name.split()[:2]).upper()
        if user_name else ""
    )
    return render_template(
        "br_intake_form.html",
        submit_url="/api/v1/intake/submit",
        project_search_url="/api/v1/projects/search",
        attach_url_base="/api/v1/attachments/inbox_items/",
        csrf_token=get_csrf_token(),
        user_name=user_name,
        user_initials=initials,
    )


@bp.route("/api/v1/intake/submit", methods=["POST"])
@login_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def br_intake_submit():
    data = request.get_json(silent=True) or {}
    rtype = (data.get("type") or "general").strip()
    if rtype not in _TYPE_META:
        return jsonify({"error": "unknown request type"}), 400

    fields = _clean_fields(data.get("fields") or {})
    missing = [
        key for key in _TYPE_META[rtype]["required"]
        if not str(fields.get(key) or "").strip()
    ]
    if missing:
        return jsonify({"error": "missing required fields", "fields": missing}), 400

    priority = (data.get("priority") or "Medium").strip()
    if priority not in _VALID_PRIORITIES:
        priority = "Medium"
    severity = (data.get("severity") or "Medium").strip()
    if severity not in _VALID_SEVERITIES:
        severity = "Medium"
    try:
        desired_by = _validate_iso_date(data.get("desired_by") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    title, body = _title_and_body(rtype, fields, priority, severity, desired_by)
    item_priority = severity if rtype == "problem" else priority
    sess = get_session()
    item = InboxItem(
        title=title,
        body=body,
        source="web-form",
        source_ref="",
        priority=item_priority,
        status="New",
        due_date=desired_by,
        created_by_user_id=session.get("user_id"),
        created_by_name=session.get("user_name") or "web-form",
    )
    sess.add(item)
    sess.flush()
    ref = f"INT-{item.id}"
    item.source_ref = ref
    log_activity(
        sess, "inbox_items", item.id, "submitted",
        new=f"web-form: {title[:80]}",
    )
    sess.commit()
    return jsonify({"ref": ref, "inbox_id": item.id}), 201


@bp.route("/api/v1/projects/search", methods=["GET"])
@login_required
def projects_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    pattern = f"%{q}%"
    sess = get_session()
    stmt = (
        select(Project)
        .where(Project.active == 1)
        .where(or_(
            Project.project_number.ilike(pattern),
            Project.name.ilike(pattern),
            Project.client.ilike(pattern),
        ))
        .order_by(Project.project_number.asc())
        .limit(10)
    )
    rows = sess.scalars(stmt).all()
    return jsonify([
        {
            "project_number": row.project_number,
            "name": row.name or "",
            "client": row.client or "",
        }
        for row in rows
    ])


@bp.route("/intake")
@intake_auth_required
def submit_hub():
    forms = [
        {
            "title": "Printable PDF / reMarkable Intake Packet",
            "copy": "Print a standard request form packet, save it as PDF, or import it into reMarkable for handwriting-first capture.",
            "queue": "Routes through OCR Capture",
            "next_step": "Scan/OCR the completed form and paste the text into the OCR landing page for TaskTrack triage.",
            "href": "/intake/printable",
            "auth_required": False,
        },
        {
            "title": "Project Work Request",
            "copy": "Submit one project-specific task, deliverable, review item, or agency/client follow-up in a clean request form.",
            "queue": "Routes to Project Tasks",
            "next_step": "Managers review scope, priority, billing phase, and due timing from the Project Tasks queue.",
            "href": "/intake/request?type=project_work",
            "auth_required": True,
        },
        {
            "title": "Weekly Project Work Submission",
            "copy": "Use this on Friday to submit next week’s project tasks in one batch.",
            "queue": "Creates Project Work tasks",
            "next_step": "A scheduler or manager reviews the batch and follows up from the Project Tasks queue.",
            "href": "/intake/request?type=project_work",
            "auth_required": True,
        },
        {
            "title": "CAD Request Submission",
            "copy": "Submit CAD changes, fixes, or manager follow-up requests without opening the dashboard.",
            "queue": "Routes to CAD Dev",
            "next_step": "Managers triage priority, assign follow-up, and track status in the dashboard.",
            "href": "/intake/request?type=cad",
            "auth_required": True,
        },
        {
            "title": "Training Request Submission",
            "copy": "Submit coaching and training needs as planned work items.",
            "queue": "Routes to Training",
            "next_step": "The request becomes planned coaching or training work with a skill area and goals.",
            "href": "/intake/request?type=training",
            "auth_required": True,
        },
        {
            "title": "General Follow-Up",
            "copy": "Submit office follow-ups, meeting action items, equipment notes, or management questions that need a tracked next step.",
            "queue": "Routes to Internal Follow-Up",
            "next_step": "The item lands in the internal queue for review, assignment, or conversion into a larger task.",
            "href": "/intake/request?type=general",
            "auth_required": True,
        },
        {
            "title": "Incident Report",
            "copy": "Sign-in required. Log a CAD process gap, capability shortfall, or work-related incident — 0, 1, or many people identified.",
            "queue": "Routes to Capabilities",
            "next_step": "Authenticated reports are reviewed as growth or process items before follow-up is assigned.",
            "href": "/intake/request?type=problem",
            "auth_required": True,
        },
    ]
    return render_template(
        "submit_hub.html",
        forms=forms,
        origin=request.url_root.rstrip("/"),
    )


@bp.route("/intake/review")
@login_required
def intake_review_queue():
    """Operator-facing queue for reviewing submitted intake records."""
    return render_template(
        "intake_review.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


@bp.route("/intake/printable")
@intake_auth_required
def printable_request_forms():
    """Printable intake packet for paper/PDF/reMarkable workflows."""
    requested = (request.args.get("form") or "packet").strip()
    if requested in ("", "packet", "all"):
        selected_forms = PRINTABLE_REQUEST_FORMS
        selected_key = "packet"
    else:
        selected_forms = [row for row in PRINTABLE_REQUEST_FORMS if row["key"] == requested]
        if not selected_forms:
            abort(404)
        selected_key = requested
    layout = (request.args.get("layout") or "letter").strip().lower()
    if layout not in {"letter", "remarkable"}:
        layout = "letter"
    return render_template(
        "printable_intake_forms.html",
        forms=selected_forms,
        all_forms=PRINTABLE_REQUEST_FORMS,
        selected_key=selected_key,
        layout=layout,
    )


def _ocr_request_payload():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    source_ref = (data.get("source_ref") or "").strip()
    if not text:
        return data, text, source_ref, jsonify({"error": "text is required"}), 400
    return data, text, source_ref, None, 200


@bp.route("/api/v1/intake/ocr/parse", methods=["POST"])
@login_required
def parse_ocr_intake():
    _data, text, source_ref, error_response, status = _ocr_request_payload()
    if error_response is not None:
        return error_response, status
    parsed = parse_printable_form_ocr(text, source_ref=source_ref)
    return jsonify(parsed)


@bp.route("/api/v1/intake/ocr/create", methods=["POST"])
@login_required
def create_ocr_intake():
    _data, text, source_ref, error_response, status = _ocr_request_payload()
    if error_response is not None:
        return error_response, status

    parsed = parse_printable_form_ocr(text, source_ref=source_ref)
    table, payload, error = printable_form_record_payload(
        parsed,
        created_by_user_id=session.get("user_id"),
        created_by_name=session.get("user_name", ""),
    )
    if error:
        return jsonify({"error": error, "parsed": parsed}), 400

    sess = get_session()
    new_id, create_error = create_direct_record(
        sess,
        table,
        payload,
        "OCR Intake",
        action="created",
        action_detail=f"OCR form ({parsed.get('form_id') or table})",
    )
    if create_error:
        sess.rollback()
        return jsonify({"error": create_error, "parsed": parsed}), 400
    sess.commit()
    return jsonify({
        "created": {"table": table, "id": new_id},
        "parsed": parsed,
    }), 201


@bp.route("/intake/project-work", methods=["GET", "POST"])
@intake_auth_required
def submit_project_work():
    return _redirect_to_request("project_work")


@bp.route("/intake/project-request", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_project_request():
    return _redirect_to_request("project_work")


@bp.route("/intake/general-follow-up", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_general_followup():
    return _redirect_to_request("general")


@bp.route("/intake/cad-development", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_cad_development():
    return _redirect_to_request("cad")


@bp.route("/intake/training", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_training():
    return _redirect_to_request("training")


# Capability submissions have been REMOVED from the intake surface
# (decision 2026-04-26 — HR-adjacent data only via authenticated UI).
# /submit/capability + /intake/capability both return 404.


@bp.route("/intake/incident", methods=["GET", "POST"])
@login_required  # Phase-5.5: auth-gated successor to the retired capability form.
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_incident():
    return _redirect_to_request("problem")
