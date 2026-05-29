"""Internal request intake forms.

Routes live at /intake/* (renamed from /submit/*). The legacy /submit/*
paths still resolve via a 308 redirect installed in app/__init__.py.

Capability submissions are not part of the intake surface — they land
via the authenticated dashboard only.

Per-route rate limits run off INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP
(default 60/hr per IP). Limits apply only on POST submissions; GETs
stay browseable.
"""
import secrets
from datetime import date, datetime
from functools import wraps

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    session,
)

from .. import limiter
from .. import profile as _profile
from ..auth import login_required
from ..config import ALLOWED_TABLES, SIMPLE_SUBMISSION_CONFIGS
from ..db import get_session
from ..services.ocr_forms import (
    PRINTABLE_REQUEST_FORMS,
    parse_printable_form_ocr,
    printable_form_record_payload,
)
from ..services.tickets import build_weekly_submission_rows, create_direct_record

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
            "href": "/intake/project-request",
            "auth_required": False,
        },
        {
            "title": "Weekly Project Work Submission",
            "copy": "Use this on Friday to submit next week’s project tasks in one batch.",
            "queue": "Creates Project Work tasks",
            "next_step": "A scheduler or manager reviews the batch and follows up from the Project Tasks queue.",
            "href": "/intake/project-work",
            "auth_required": False,
        },
        {
            "title": "CAD Request Submission",
            "copy": "Submit CAD changes, fixes, or manager follow-up requests without opening the dashboard.",
            "queue": "Routes to CAD Dev",
            "next_step": "Managers triage priority, assign follow-up, and track status in the dashboard.",
            "href": "/intake/cad-development",
            "auth_required": False,
        },
        {
            "title": "Training Request Submission",
            "copy": "Submit coaching and training needs as planned work items.",
            "queue": "Routes to Training",
            "next_step": "The request becomes planned coaching or training work with a skill area and goals.",
            "href": "/intake/training",
            "auth_required": False,
        },
        {
            "title": "General Follow-Up",
            "copy": "Submit office follow-ups, meeting action items, equipment notes, or management questions that need a tracked next step.",
            "queue": "Routes to Internal Follow-Up",
            "next_step": "The item lands in the internal queue for review, assignment, or conversion into a larger task.",
            "href": "/intake/general-follow-up",
            "auth_required": False,
        },
        {
            "title": "Incident Report",
            "copy": "Sign-in required. Log a CAD process gap, capability shortfall, or work-related incident — 0, 1, or many people identified.",
            "queue": "Routes to Capabilities",
            "next_step": "Authenticated reports are reviewed as growth or process items before follow-up is assigned.",
            "href": "/intake/incident",
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
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_project_work():
    rows = build_weekly_submission_rows(request.form if request.method == "POST" else None)
    submitter_name = (request.form.get("submitter_name") or "").strip() if request.method == "POST" else ""
    week_of = (request.form.get("week_of") or "").strip() if request.method == "POST" else date.today().isoformat()
    error = None
    success = None

    if request.method == "POST":
        if not submitter_name:
            error = "Your Name is required."
        elif not week_of:
            error = "Week Of is required."
        else:
            db = get_session()
            created_count = 0
            batch_id = f"weekly-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"

            for idx, row in enumerate(rows, start=1):
                if not any(row.values()):
                    continue

                payload = {
                    "project_name": row["project_name"],
                    "title": row["title"],
                    "project_number": row["project_number"],
                    "billing_phase": row["billing_phase"],
                    "engineer": row["engineer"],
                    "task_description": row["task_description"],
                    "due_at": row["due_at"],
                    "notes": (
                        f"Submitted via Weekly Work Submission\n"
                        f"Submitted by: {submitter_name}\n"
                        f"Week of: {week_of}\n"
                        f"Batch: {batch_id}"
                    ),
                }
                payload.update({
                    "created_by_user_id": session.get("user_id"),
                    "created_by_name": session.get("user_name") or "Weekly Work Submission",
                    "status": ALLOWED_TABLES["project_work_tasks"]["status_flow"][0],
                    "priority": "Medium",
                })

                _, row_error = create_direct_record(
                    db,
                    "project_work_tasks",
                    payload,
                    "Weekly Work Submission",
                    action="submitted",
                    action_detail=f"{submitter_name} | {week_of}",
                )
                if row_error:
                    error = f"Project Task {idx}: {row_error}"
                    break
                created_count += 1

            if error:
                db.rollback()
            elif created_count == 0:
                error = "Fill out at least one project task before submitting."
            else:
                db.commit()
                success = f"Submitted {created_count} project task{'s' if created_count != 1 else ''} for the week of {week_of}."
                rows = build_weekly_submission_rows(None)
                submitter_name = ""
                week_of = date.today().isoformat()

    return render_template(
        "weekly_submit.html",
        rows=rows,
        submitter_name=submitter_name,
        week_of=week_of,
        error=error,
        success=success,
    )


def _render_simple_submission(config_key):
    config = SIMPLE_SUBMISSION_CONFIGS[config_key]
    values = {
        field["name"]: (request.form.get(field["name"]) or "").strip()
        for field in config["fields"]
    } if request.method == "POST" else {}
    error = None
    success = None

    if request.method == "POST":
        payload = {}
        for field in config["fields"]:
            value = (request.form.get(field["name"]) or "").strip()
            payload[field["name"]] = value

        payload.update({
            "created_by_user_id": session.get("user_id"),
            "created_by_name": session.get("user_name") or config["source_name"],
            "status": ALLOWED_TABLES[config["table"]]["status_flow"][0],
        })

        table_fields = ALLOWED_TABLES[config["table"]]["fields"]
        if "source" in table_fields and not payload.get("source"):
            payload["source"] = config.get("source", "web-form")
        if "needs_review" in table_fields and config.get("needs_review", True):
            payload["needs_review"] = 1
        if "priority" in table_fields and not payload.get("priority"):
            payload["priority"] = "Medium"
        if config["table"] == "personnel_issues" and not payload.get("severity"):
            payload["severity"] = "Medium"

        db = get_session()
        _, error = create_direct_record(
            db,
            config["table"],
            payload,
            config["source_name"],
            action="submitted",
            action_detail=payload.get("submitted_by") or payload.get("requested_by") or payload.get("observed_by") or config["source_name"],
        )
        if error:
            db.rollback()
        else:
            db.commit()
            success = f"{config['success_noun'].capitalize()} submitted successfully."
            values = {}

    return render_template(
        "simple_submit.html",
        config=config,
        values=values,
        error=error,
        success=success,
        form_url=request.url,
    )


@bp.route("/intake/project-request", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_project_request():
    return _render_simple_submission("project-request")


@bp.route("/intake/general-follow-up", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_general_followup():
    return _render_simple_submission("general-follow-up")


@bp.route("/intake/cad-development", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_cad_development():
    return _render_simple_submission("cad-development")


@bp.route("/intake/training", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_training():
    return _render_simple_submission("training")


# Capability submissions have been REMOVED from the intake surface
# (decision 2026-04-26 — HR-adjacent data only via authenticated UI).
# /submit/capability + /intake/capability both return 404.


@bp.route("/intake/incident", methods=["GET", "POST"])
@login_required  # Phase-5.5: auth-gated successor to the retired capability form.
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_incident():
    """Auth-required incident report. The form accepts a comma-separated
    list in `person_name`; the service-layer enrich_with_fks resolves
    matching Employees and writes person_ids."""
    return _render_simple_submission("incident")
