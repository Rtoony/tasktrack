"""Bot-scoped project read endpoints for Hermes's project-note sync.

Project enumeration + per-project workspace are otherwise cookie-only
(@login_required in registry.py); these give the Hermes bot token read access to
the same data with is_admin=False redaction (personnel_issues + internal/private
fields stripped) — safe because the resulting notes are pushed to GitHub and
RAG-ingested.

    GET /api/v1/projects/bot?active=1&q=&limit=    -> thin active-project list
    GET /api/v1/projects/bot/<number>/note-data    -> full workspace payload (redacted)

This stays a THIN read of existing columns + the existing redaction path. It does
NOT add or expose the OrdoCAD-locked "CAD Project Setup" metadata (jurisdictions,
stakeholders, area boundaries, sheet indices) — honors the 2026-05-22
TaskTrack<->OrdoCAD separation. Hermes is the PM/assistant lane.
"""
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from .. import limiter
from ..db import get_session
from ..models import Project, to_dict
from ..services.project_workspace import project_workspace_payload
from ..tokens import check_scoped_token

bp = Blueprint("agent_projects", __name__)

_THIN_FIELDS = ("project_number", "name", "client", "component", "principal",
                "display_status", "active")


def _skip_limit_for_tests() -> bool:
    return bool(current_app.config.get("TESTING"))


@bp.route("/api/v1/projects/bot", methods=["GET"])
@limiter.limit("60 per minute; 600 per hour", exempt_when=_skip_limit_for_tests)
def projects_bot():
    err = check_scoped_token("bot")
    if err:
        return err
    want_active = request.args.get("active", "1") != "0"
    q = (request.args.get("q") or "").strip().lower()
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200

    sess = get_session()
    stmt = select(Project)
    if want_active:
        stmt = stmt.where(Project.active == 1)
    stmt = stmt.order_by(Project.project_number.asc())

    out: list[dict] = []
    for p in sess.scalars(stmt).all():
        d = to_dict(p) or {}
        if q and q not in str(d.get("project_number", "")).lower() \
                and q not in str(d.get("name", "")).lower():
            continue
        out.append({k: d.get(k, "") for k in _THIN_FIELDS})
        if len(out) >= limit:
            break
    return jsonify({"projects": out, "count": len(out)})


@bp.route("/api/v1/projects/bot/<number>/note-data", methods=["GET"])
@limiter.limit("60 per minute; 600 per hour", exempt_when=_skip_limit_for_tests)
def project_note_data(number):
    err = check_scoped_token("bot")
    if err:
        return err
    sess = get_session()
    proj = sess.scalars(
        select(Project).where(Project.project_number == number)
    ).first()
    if proj is None:
        return jsonify({"error": "project not found", "project_number": number}), 404
    # is_admin=False strips personnel_issues + internal_notes + private calendar
    # bodies — the notes leave the box (GitHub + RAG), so no sensitive content.
    payload = project_workspace_payload(sess, proj, user_id=None, is_admin=False)
    return jsonify(payload)
