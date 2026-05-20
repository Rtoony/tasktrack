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
from datetime import datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from ..auth import admin_required
from ..db import get_session
from ..models import Employee, Project, to_dict

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
@admin_required
def list_projects():
    sess = get_session()
    include_inactive = request.args.get("include_inactive") in ("1", "true", "yes")
    stmt = select(Project).order_by(Project.project_number.asc())
    if not include_inactive:
        stmt = stmt.where(Project.active == 1)
    rows = sess.scalars(stmt).all()
    return jsonify([to_dict(r) for r in rows])


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
    proj = Project(
        project_number=project_number,
        name=(data.get("name") or "").strip(),
        client=(data.get("client") or "").strip(),
        billing_phase_default=(data.get("billing_phase_default") or "").strip(),
        external_ref=(data.get("external_ref") or "").strip(),
        external_system=(data.get("external_system") or "").strip(),
        notes=(data.get("notes") or "").strip(),
        active=1 if data.get("active", 1) else 0,
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
                "external_ref", "external_system", "notes"):
        if col in data:
            val = (data[col] or "").strip()
            if col == "project_number" and not val:
                return jsonify({"error": "project_number cannot be blank",
                                "request_id": _rid()}), 400
            setattr(proj, col, val)
    if "active" in data:
        proj.active = 1 if data["active"] else 0
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
