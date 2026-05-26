"""Employees + Projects registry (Phase 0).

Admin-only CRUD for the new FK spine. Lists are scoped to active rows
by default (`?include_inactive=1` to see all).

Routes mirror the rest of /api/v1/*:
- GET    /api/v1/employees
- POST   /api/v1/employees
- GET    /api/v1/employees/<id>
- PATCH  /api/v1/employees/<id>
- DELETE /api/v1/employees/<id>   (soft delete: sets active=0)
- (same shape for /api/v1/projects)
"""
import json
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select

from ..auth import admin_required, login_required
from ..db import get_session
from ..models import (
    CalendarEvent,
    Employee,
    PersonnelIssue,
    Project,
    ProjectSite,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
    to_dict,
)
from ..services.convex_hull import hull_geojson_ring

# Pared down to {active, dormant} to match the firm's Master Project List
# spreadsheet, which is now the source of truth for project state. The
# old completed/draft/review options pre-dated the master-list import
# and weren't actually in use (projects table was empty when the import
# ran).
_PROJECT_DISPLAY_STATUSES = {"active", "dormant"}


def _coerce_latlng(raw):
    """Parse a string/number latitude or longitude; return None if blank/bad."""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _project_identity_filters():
    project_id_q = (request.args.get("project_id") or "").strip()
    project_number = (request.args.get("project_number") or "").strip()
    project_id = None
    if project_id_q:
        try:
            project_id = int(project_id_q)
        except (TypeError, ValueError):
            return None, None, jsonify({
                "error": "project_id must be an integer",
                "request_id": _rid(),
            }), 400
    return project_id, project_number, None, None


def _project_filter_stmt(stmt, *, include_inactive: bool, component: str = "",
                         client_q: str = "", ds_q: str = "",
                         project_id: int | None = None,
                         project_number: str = ""):
    if not include_inactive:
        stmt = stmt.where(Project.active == 1)
    if component:
        stmt = stmt.where(Project.component == component)
    if client_q:
        stmt = stmt.where(Project.client.ilike(f"%{client_q}%"))
    if ds_q:
        stmt = stmt.where(Project.display_status == ds_q)
    if project_id is not None:
        stmt = stmt.where(Project.id == project_id)
    if project_number:
        stmt = stmt.where(Project.project_number == project_number)
    return stmt


def _linked_rows(sess, model, project_id: int, project_number: str, *, limit: int = 50):
    stmt = select(model).where(
        (model.project_id == project_id) | (model.project_number == project_number)
    ).order_by(model.id.desc()).limit(limit)
    return [to_dict(row) for row in sess.scalars(stmt).all()]

bp = Blueprint("registry", __name__)


def _rid():
    return g.get("request_id", "-")


# ── Employees ─────────────────────────────────────────────────────────────


@bp.route("/api/v1/employees", methods=["GET"])
@admin_required
def list_employees():
    sess = get_session()
    include_inactive = request.args.get("include_inactive") in ("1", "true", "yes")
    stmt = select(Employee).order_by(Employee.display_name.asc())
    if not include_inactive:
        stmt = stmt.where(Employee.active == 1)
    rows = sess.scalars(stmt).all()
    return jsonify([to_dict(r) for r in rows])


@bp.route("/api/v1/employees", methods=["POST"])
@admin_required
def create_employee():
    data = request.get_json(silent=True) or {}
    name = (data.get("display_name") or "").strip()
    if not name:
        return jsonify({"error": "display_name is required",
                        "request_id": _rid()}), 400
    sess = get_session()
    emp = Employee(
        display_name=name,
        email=(data.get("email") or "").strip(),
        role=(data.get("role") or "").strip(),
        title=(data.get("title") or "").strip(),
        notes=(data.get("notes") or "").strip(),
        active=1 if data.get("active", 1) else 0,
    )
    sess.add(emp)
    sess.commit()
    return jsonify(to_dict(emp)), 201


@bp.route("/api/v1/employees/<int:emp_id>", methods=["GET"])
@admin_required
def get_employee(emp_id):
    sess = get_session()
    emp = sess.get(Employee, emp_id)
    if emp is None:
        return jsonify({"error": "not found", "request_id": _rid()}), 404
    return jsonify(to_dict(emp))


@bp.route("/api/v1/employees/<int:emp_id>", methods=["PATCH"])
@admin_required
def update_employee(emp_id):
    sess = get_session()
    emp = sess.get(Employee, emp_id)
    if emp is None:
        return jsonify({"error": "not found", "request_id": _rid()}), 404
    data = request.get_json(silent=True) or {}
    for col in ("display_name", "email", "role", "title", "notes"):
        if col in data:
            val = (data[col] or "").strip()
            if col == "display_name" and not val:
                return jsonify({"error": "display_name cannot be blank",
                                "request_id": _rid()}), 400
            setattr(emp, col, val)
    if "active" in data:
        emp.active = 1 if data["active"] else 0
    emp.updated_at = datetime.utcnow()
    sess.commit()
    return jsonify(to_dict(emp))


@bp.route("/api/v1/employees/<int:emp_id>", methods=["DELETE"])
@admin_required
def delete_employee(emp_id):
    """Soft delete — sets active=0. Existing FK references stay valid."""
    sess = get_session()
    emp = sess.get(Employee, emp_id)
    if emp is None:
        return jsonify({"error": "not found", "request_id": _rid()}), 404
    emp.active = 0
    emp.updated_at = datetime.utcnow()
    sess.commit()
    return jsonify({"deactivated": emp_id})


# ── Projects ──────────────────────────────────────────────────────────────


@bp.route("/api/v1/projects", methods=["GET"])
@login_required
def list_projects():
    """Read-only project list — open to every logged-in user.

    The fk-select widgets in the task modals + the Map tab + the
    per-project mini-map all rely on this. Mutations remain admin-only.
    """
    sess = get_session()
    include_inactive = request.args.get("include_inactive") in ("1", "true", "yes")
    stmt = select(Project).order_by(Project.project_number.asc())
    if not include_inactive:
        stmt = stmt.where(Project.active == 1)
    rows = sess.scalars(stmt).all()
    return jsonify([to_dict(r) for r in rows])


@bp.route("/api/v1/projects/geojson", methods=["GET"])
@login_required
def projects_geojson():
    """GeoJSON FeatureCollection for the Map tab.

    Emits one Feature per `project_sites` row so multi-site projects (the
    Master List has ~360 of these, worst case 84 sites for "223.00") all
    render. Falls back to the legacy `projects.lat`/`projects.lng`
    columns for any project without a row in `project_sites` — e.g.
    projects added through the admin UI after the import.

    Optional query params (all AND'd together):
        ?bbox=west,south,east,north   bounding-box clip
        ?component=<exact>            filter by project Component
        ?client=<substring>           case-insensitive client substring
        ?display_status=active|dormant
        ?pin_color=yellow|red|green|blue|pink   per-site artifact color
        ?include_inactive=1           include soft-deleted projects
    """
    sess = get_session()
    include_inactive = request.args.get("include_inactive") in ("1", "true", "yes")

    component = (request.args.get("component") or "").strip()
    client_q = (request.args.get("client") or "").strip()
    ds_q = (request.args.get("display_status") or "").strip()
    pin_color = (request.args.get("pin_color") or "").strip()
    project_id, project_number, err, code = _project_identity_filters()
    if err is not None:
        return err, code

    bbox = request.args.get("bbox", "")
    bbox_vals = None
    if bbox:
        try:
            bbox_vals = tuple(float(x) for x in bbox.split(","))
            if len(bbox_vals) != 4:
                bbox_vals = None
        except (ValueError, TypeError):
            bbox_vals = None

    def _apply_project_filters(stmt):
        return _project_filter_stmt(
            stmt,
            include_inactive=include_inactive,
            component=component,
            client_q=client_q,
            ds_q=ds_q,
            project_id=project_id,
            project_number=project_number,
        )

    features = []

    # 1) One feature per project_sites row (the common path, ~5,400 pins).
    site_stmt = (
        select(ProjectSite, Project)
        .join(Project, Project.id == ProjectSite.project_id)
    )
    site_stmt = _apply_project_filters(site_stmt)
    if pin_color:
        site_stmt = site_stmt.where(ProjectSite.pin_color == pin_color)
    if bbox_vals is not None:
        west, south, east, north = bbox_vals
        site_stmt = site_stmt.where(
            ProjectSite.lng >= west, ProjectSite.lng <= east,
            ProjectSite.lat >= south, ProjectSite.lat <= north,
        )

    seen_project_ids: set[int] = set()
    for site, proj in sess.execute(site_stmt).all():
        seen_project_ids.add(proj.id)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [site.lng, site.lat]},
            "properties": {
                "project_id":     proj.id,
                "site_id":        site.id,
                "project_number": proj.project_number,
                "name":           proj.name,
                "client":         proj.client,
                "component":      proj.component,
                "principal":      proj.principal,
                "display_status": proj.display_status,
                "pin_color":      site.pin_color,
                "is_primary":     bool(site.is_primary),
                "active":         bool(proj.active),
            },
        })

    # 2) Fallback: projects with legacy lat/lng but no project_sites row
    # (e.g. ones added via admin UI after the master-list import). Skip
    # them if a pin_color filter is in effect since they have no color.
    if not pin_color:
        legacy_stmt = select(Project).where(
            Project.lat.is_not(None), Project.lng.is_not(None),
        )
        legacy_stmt = _apply_project_filters(legacy_stmt)
        if bbox_vals is not None:
            west, south, east, north = bbox_vals
            legacy_stmt = legacy_stmt.where(
                Project.lng >= west, Project.lng <= east,
                Project.lat >= south, Project.lat <= north,
            )
        for proj in sess.scalars(legacy_stmt).all():
            if proj.id in seen_project_ids:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [proj.lng, proj.lat]},
                "properties": {
                    "project_id":     proj.id,
                    "site_id":        None,
                    "project_number": proj.project_number,
                    "name":           proj.name,
                    "client":         proj.client,
                    "component":      proj.component,
                    "principal":      proj.principal,
                    "display_status": proj.display_status,
                    "pin_color":      "",
                    "is_primary":     True,
                    "active":         bool(proj.active),
                },
            })

    return jsonify({"type": "FeatureCollection", "features": features})


@bp.route("/api/v1/projects/hulls", methods=["GET"])
@login_required
def projects_hulls():
    """GeoJSON FeatureCollection of convex hulls for multi-site projects.

    Rendering-only feature for the Map tab — gives a "general area"
    view of projects that span more than one location, without
    requiring any new schema (the OrdoCAD lane separation explicitly
    forbids adding per-project boundary metadata to TaskTrack; this
    just visualizes the existing project_sites rows differently).

    Only projects with **3 or more sites** get a Polygon. Two-site
    projects yield a degenerate line and are skipped — they remain
    visible as the two individual pins via the geojson endpoint.

    Honors the same filter params as /api/v1/projects/geojson so the
    hull layer stays in sync with the pin layer:
        ?component=<exact>
        ?client=<substring>
        ?display_status=active|dormant
        ?include_inactive=1
        ?pin_color=...   (filters which SITES contribute to the hull;
                          a project with sites of multiple colors will
                          still get a hull computed from the matching
                          sites only)

    No bbox clipping — the hull may legitimately extend outside the
    current viewport (that's its purpose). The frontend can z-cull.
    """
    sess = get_session()
    include_inactive = request.args.get("include_inactive") in ("1", "true", "yes")
    component = (request.args.get("component") or "").strip()
    client_q = (request.args.get("client") or "").strip()
    ds_q = (request.args.get("display_status") or "").strip()
    pin_color = (request.args.get("pin_color") or "").strip()
    project_id, project_number, err, code = _project_identity_filters()
    if err is not None:
        return err, code

    stmt = (
        select(ProjectSite, Project)
        .join(Project, Project.id == ProjectSite.project_id)
    )
    stmt = _project_filter_stmt(
        stmt,
        include_inactive=include_inactive,
        component=component,
        client_q=client_q,
        ds_q=ds_q,
        project_id=project_id,
        project_number=project_number,
    )
    if pin_color:
        stmt = stmt.where(ProjectSite.pin_color == pin_color)

    # Bucket sites by project_id, keep one Project ref per bucket.
    by_project: dict[int, dict] = {}
    for site, proj in sess.execute(stmt).all():
        bucket = by_project.setdefault(
            proj.id,
            {"proj": proj, "points": []},
        )
        bucket["points"].append((site.lng, site.lat))

    features = []
    for pid, bucket in by_project.items():
        if len(bucket["points"]) < 3:
            continue
        ring = hull_geojson_ring(bucket["points"])
        if ring is None:
            continue
        proj = bucket["proj"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "project_id":     proj.id,
                "project_number": proj.project_number,
                "name":           proj.name,
                "client":         proj.client,
                "component":      proj.component,
                "principal":      proj.principal,
                "display_status": proj.display_status,
                "site_count":     len(bucket["points"]),
                "active":         bool(proj.active),
            },
        })

    return jsonify({"type": "FeatureCollection", "features": features})


@bp.route("/api/v1/projects/components", methods=["GET"])
@login_required
def project_components():
    """Distinct (component, count) pairs for the Map-tab filter dropdown.

    Excludes the empty string so the dropdown only lists real types.
    Honors `?include_inactive=1` the same way the geojson endpoint does
    so the counts match what the user can see.
    """
    sess = get_session()
    include_inactive = request.args.get("include_inactive") in ("1", "true", "yes")
    stmt = (
        select(Project.component, func.count(Project.id))
        .where(Project.component != "")
        .group_by(Project.component)
        .order_by(func.count(Project.id).desc())
    )
    if not include_inactive:
        stmt = stmt.where(Project.active == 1)
    rows = [{"component": c, "count": n} for c, n in sess.execute(stmt).all()]
    return jsonify(rows)


def _master_sync_state_path() -> Path:
    """Mirror scripts/sync_master_if_changed.py's _state_path()
    convention. Kept in lockstep by hand — small enough that pulling
    in the script module just for one helper isn't worth it."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "tasktrack" / "master-sync.json"


@bp.route("/api/v1/projects/sync-status", methods=["GET"])
@login_required
def projects_sync_status():
    """Surface the last master-list sync run for the admin badge.

    Returns the state file's contents verbatim (last_run_at, hashes,
    compact summary) plus a `state` field with one of:
      - "never_run"         no state file exists yet
      - "unreadable"        state file exists but couldn't be parsed
      - "ok"                state file parsed cleanly
    """
    state_path = _master_sync_state_path()
    if not state_path.is_file():
        return jsonify({
            "state": "never_run",
            "message": "The automated sync has not run yet on this host.",
        })
    try:
        payload = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({
            "state":   "unreadable",
            "message": f"State file present but could not be parsed: {exc}",
        }), 500
    payload["state"] = "ok"
    return jsonify(payload)




def _project_workspace_payload(sess, proj: Project) -> dict:
    sites = [
        to_dict(row)
        for row in sess.scalars(
            select(ProjectSite)
            .where(ProjectSite.project_id == proj.id)
            .order_by(ProjectSite.is_primary.desc(), ProjectSite.id.asc())
        ).all()
    ]
    linked = {
        "work_tasks": _linked_rows(sess, WorkTask, proj.id, proj.project_number),
        "project_work_tasks": _linked_rows(sess, ProjectWorkTask, proj.id, proj.project_number),
        "training_tasks": _linked_rows(sess, TrainingTask, proj.id, proj.project_number),
        "personnel_issues": _linked_rows(sess, PersonnelIssue, proj.id, proj.project_number),
        "calendar_events": _linked_rows(sess, CalendarEvent, proj.id, proj.project_number),
    }
    return {
        "project": to_dict(proj),
        "sites": sites,
        "linked_records": linked,
        "counts": {key: len(value) for key, value in linked.items()} | {
            "sites": len(sites),
        },
        "external": {
            "system": proj.external_system or "",
            "ref": proj.external_ref or "",
        },
    }


@bp.route("/api/v1/projects/<int:proj_id>/workspace", methods=["GET"])
@login_required
def project_workspace_by_id(proj_id):
    sess = get_session()
    proj = sess.get(Project, proj_id)
    if proj is None:
        return jsonify({"error": "not found", "request_id": _rid()}), 404
    return jsonify(_project_workspace_payload(sess, proj))


@bp.route("/api/v1/projects/workspace", methods=["GET"])
@login_required
def project_workspace_by_number():
    project_number = (request.args.get("project_number") or "").strip()
    if not project_number:
        return jsonify({"error": "project_number is required", "request_id": _rid()}), 400
    sess = get_session()
    proj = sess.scalar(select(Project).where(Project.project_number == project_number))
    if proj is None:
        return jsonify({"error": "not found", "request_id": _rid()}), 404
    return jsonify(_project_workspace_payload(sess, proj))

@bp.route("/api/v1/projects", methods=["POST"])
@admin_required
def create_project():
    data = request.get_json(silent=True) or {}
    project_number = (data.get("project_number") or "").strip()
    if not project_number:
        return jsonify({"error": "project_number is required",
                        "request_id": _rid()}), 400
    sess = get_session()
    existing = sess.scalar(
        select(Project).where(Project.project_number == project_number)
    )
    if existing is not None:
        return jsonify({"error": "project_number already exists",
                        "existing_id": existing.id,
                        "request_id": _rid()}), 409
    display_status = (data.get("display_status") or "active").strip()
    if display_status not in _PROJECT_DISPLAY_STATUSES:
        return jsonify({
            "error": f"display_status must be one of: {sorted(_PROJECT_DISPLAY_STATUSES)}",
            "request_id": _rid(),
        }), 400
    proj = Project(
        project_number=project_number,
        name=(data.get("name") or "").strip(),
        client=(data.get("client") or "").strip(),
        billing_phase_default=(data.get("billing_phase_default") or "").strip(),
        external_ref=(data.get("external_ref") or "").strip(),
        external_system=(data.get("external_system") or "").strip(),
        notes=(data.get("notes") or "").strip(),
        component=(data.get("component") or "").strip(),
        principal=(data.get("principal") or "").strip(),
        start_date=(data.get("start_date") or "").strip(),
        dormant_date=(data.get("dormant_date") or "").strip(),
        active=1 if data.get("active", 1) else 0,
        lat=_coerce_latlng(data.get("lat")),
        lng=_coerce_latlng(data.get("lng")),
        display_status=display_status,
    )
    sess.add(proj)
    sess.commit()
    return jsonify(to_dict(proj)), 201


@bp.route("/api/v1/projects/<int:proj_id>", methods=["GET"])
@admin_required
def get_project(proj_id):
    sess = get_session()
    proj = sess.get(Project, proj_id)
    if proj is None:
        return jsonify({"error": "not found", "request_id": _rid()}), 404
    return jsonify(to_dict(proj))


@bp.route("/api/v1/projects/<int:proj_id>", methods=["PATCH"])
@admin_required
def update_project(proj_id):
    sess = get_session()
    proj = sess.get(Project, proj_id)
    if proj is None:
        return jsonify({"error": "not found", "request_id": _rid()}), 404
    data = request.get_json(silent=True) or {}
    for col in ("project_number", "name", "client", "billing_phase_default",
                "external_ref", "external_system", "notes",
                "component", "principal", "start_date", "dormant_date"):
        if col in data:
            val = (data[col] or "").strip()
            if col == "project_number" and not val:
                return jsonify({"error": "project_number cannot be blank",
                                "request_id": _rid()}), 400
            setattr(proj, col, val)
    if "active" in data:
        proj.active = 1 if data["active"] else 0
    if "lat" in data:
        proj.lat = _coerce_latlng(data["lat"])
    if "lng" in data:
        proj.lng = _coerce_latlng(data["lng"])
    if "display_status" in data:
        ds = (data["display_status"] or "").strip()
        if ds not in _PROJECT_DISPLAY_STATUSES:
            return jsonify({
                "error": f"display_status must be one of: {sorted(_PROJECT_DISPLAY_STATUSES)}",
                "request_id": _rid(),
            }), 400
        proj.display_status = ds
    proj.updated_at = datetime.utcnow()
    sess.commit()
    return jsonify(to_dict(proj))


@bp.route("/api/v1/projects/<int:proj_id>", methods=["DELETE"])
@admin_required
def delete_project(proj_id):
    """Soft delete — sets active=0. Existing FK references stay valid."""
    sess = get_session()
    proj = sess.get(Project, proj_id)
    if proj is None:
        return jsonify({"error": "not found", "request_id": _rid()}), 404
    proj.active = 0
    proj.updated_at = datetime.utcnow()
    sess.commit()
    return jsonify({"deactivated": proj_id})
