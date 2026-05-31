"""Top-level dashboard + healthcheck."""
from datetime import datetime, UTC
import os
from pathlib import Path
import subprocess

from flask import Blueprint, current_app, g, jsonify, render_template, session

from ..auth import login_required

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        standalone_tab=None,
        standalone_title=None,
        standalone_subtitle=None,
    )


@bp.route("/capture/ocr")
@login_required
def capture_ocr():
    return render_template(
        "capture_ocr.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


@bp.route("/testing")
@login_required
def testing_checklist():
    return render_template(
        "testing_checklist.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


def _git_value(args: list[str]) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return ""


@bp.route("/api/v1/app-context")
@login_required
def app_context():
    """Small diagnostic payload for feedback records.

    This intentionally excludes secrets, request bodies, and full paths.
    The goal is to pin a feedback item to the running app/build context.
    """
    dirty = bool(_git_value(["status", "--short"]))
    return jsonify({
        "app": "tasktrack",
        "brand": current_app.config.get("BRAND_NAME", "TaskTrack"),
        "server_time": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "request_id": g.get("request_id", ""),
        "git": {
            "commit": _git_value(["rev-parse", "HEAD"]),
            "short_commit": _git_value(["rev-parse", "--short", "HEAD"]),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "dirty": dirty,
        },
        "runtime": {
            "profile": current_app.config.get("PROFILE", ""),
            "db_name": Path(current_app.config.get("DB_PATH", "tracker.db")).name,
            "build_id": os.environ.get("TASKTRACK_BUILD_ID", ""),
        },
    })


@bp.route("/healthz")
def healthz():
    return "ok"
