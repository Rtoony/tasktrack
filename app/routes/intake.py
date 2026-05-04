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
    Blueprint, g, jsonify, redirect, render_template,
    request, session, url_for,
)

from .. import limiter
from .. import profile as _profile
from ..config import ALLOWED_TABLES, SIMPLE_SUBMISSION_CONFIGS
from ..db import get_session
from ..services.tickets import build_weekly_submission_rows, create_direct_record

bp = Blueprint("intake", __name__)


def _intake_post_limit():
    return f"{_profile.INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP} per hour"


def intake_auth_required(f):
    """No-op decorator — intake is open. Kept as a seam in case the personal
    install ever fronts intake URLs publicly and Josh wants to flip it."""

    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


@bp.route("/intake")
@intake_auth_required
def submit_hub():
    forms = [
        {
            "title": "Weekly Project Work Submission",
            "copy": "Use this on Friday to submit next week’s project tasks in one batch.",
            "href": "/intake/project-work",
        },
        {
            "title": "CAD Request Submission",
            "copy": "Submit CAD changes, fixes, or manager follow-up requests without opening the dashboard.",
            "href": "/intake/cad-development",
        },
        {
            "title": "Training Request Submission",
            "copy": "Submit coaching and training needs as planned work items.",
            "href": "/intake/training",
        },
        {
            "title": "Suggestion Box",
            "copy": "Collect ideas for training, templates, standards, automation, and process improvements.",
            "href": "/intake/suggestion-box",
        },
    ]
    return render_template("submit_hub.html", forms=forms)


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

        if "priority" in ALLOWED_TABLES[config["table"]]["fields"] and not payload.get("priority"):
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
    )


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


@bp.route("/intake/suggestion-box", methods=["GET", "POST"])
@intake_auth_required
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_suggestion_box():
    return _render_simple_submission("suggestion-box")


# Capability submissions have been REMOVED from the intake surface
# (decision 2026-04-26 — HR-adjacent data only via authenticated UI).
# /submit/capability + /intake/capability both return 404.
