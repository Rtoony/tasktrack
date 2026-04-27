"""Intake (request) forms.

Phase 1C will rename routes from /submit/* to /intake/* (with redirects)
and require login in the company profile. Capability submissions move
out of intake entirely.

Per-route rate limits run off the profile-driven
INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP setting (60/hr personal, 10/hr
company). Limits apply per-IP and only on POST submissions; GETs are
unlimited so the form pages stay browseable.
"""
import secrets
from datetime import date, datetime

from flask import Blueprint, render_template, request

from .. import limiter
from .. import profile as _profile
from ..config import ALLOWED_TABLES, SIMPLE_SUBMISSION_CONFIGS
from ..db import get_db
from ..services.tickets import build_weekly_submission_rows, create_direct_record

bp = Blueprint("intake", __name__)


def _intake_post_limit():
    return f"{_profile.INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP} per hour"


@bp.route("/submit")
def submit_hub():
    forms = [
        {
            "title": "Weekly Project Work Submission",
            "copy": "Use this on Friday to submit next week’s project tasks in one batch.",
            "href": "/submit/project-work",
        },
        {
            "title": "CAD Request Submission",
            "copy": "Submit CAD changes, fixes, or manager follow-up requests without opening the dashboard.",
            "href": "/submit/cad-development",
        },
        {
            "title": "Training Request Submission",
            "copy": "Submit coaching and training needs as planned work items.",
            "href": "/submit/training",
        },
        {
            "title": "Capability Observation Submission",
            "copy": "Document staff capability gaps or incidents that should be tracked over time.",
            "href": "/submit/capability",
        },
        {
            "title": "Suggestion Box",
            "copy": "Collect ideas for training, templates, standards, automation, and process improvements.",
            "href": "/submit/suggestion-box",
        },
    ]
    return render_template("submit_hub.html", forms=forms)


@bp.route("/submit/project-work", methods=["GET", "POST"])
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
            db = get_db()
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
                    "created_by_user_id": None,
                    "created_by_name": "Weekly Work Submission",
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
            "created_by_user_id": None,
            "created_by_name": config["source_name"],
            "status": ALLOWED_TABLES[config["table"]]["status_flow"][0],
        })

        if "priority" in ALLOWED_TABLES[config["table"]]["fields"] and not payload.get("priority"):
            payload["priority"] = "Medium"
        if config["table"] == "personnel_issues" and not payload.get("severity"):
            payload["severity"] = "Medium"

        db = get_db()
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


@bp.route("/submit/cad-development", methods=["GET", "POST"])
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_cad_development():
    return _render_simple_submission("cad-development")


@bp.route("/submit/training", methods=["GET", "POST"])
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_training():
    return _render_simple_submission("training")


@bp.route("/submit/capability", methods=["GET", "POST"])
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_capability():
    return _render_simple_submission("capability")


@bp.route("/submit/suggestion-box", methods=["GET", "POST"])
@limiter.limit(_intake_post_limit, methods=["POST"])
def submit_suggestion_box():
    return _render_simple_submission("suggestion-box")
