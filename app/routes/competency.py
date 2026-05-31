"""Competency endpoints (Phase 1) — admin-only skill matrix CRUD.

- GET  /api/v1/skills/categories      — list categories (seeds defaults
                                        on first call if table empty)
- POST /api/v1/skills/categories      — add a category
- PATCH /api/v1/skills/categories/<id> — edit category (name, desc, order, active)
- GET  /api/v1/skills/matrix          — single-fetch shape for the matrix UI:
                                        { employees, categories, scores: {emp_id: {cat_id: score}} }
- POST /api/v1/skills/scores          — upsert one (employee, category) score
- GET  /api/v1/skills/scores/<employee_id> — per-employee score list
"""
from datetime import datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from ..auth import admin_required, login_required
from ..db import get_session
from ..models import Employee, EmployeeSkillScore, EmployeeSkillSubscore, SkillCategory, to_dict
from ..services.competency import (
    CompetencyError,
    add_subscore,
    confidence_band,
    detail_for_cell,
    dimensions_for_category,
    recompute_all,
    seed_default_categories,
    upsert_score,
)

bp = Blueprint("competency", __name__)


def _rid():
    return g.get("request_id", "-")


# ── Categories ────────────────────────────────────────────────────────────


@bp.route("/api/v1/skills/categories", methods=["GET"])
@login_required
def list_categories():
    """Returns active categories ordered by display_order then name.

    Open to every logged-in user — the fk-select widget on the
    personnel-issue modal (Phase 2) pulls from here. Mutations stay
    admin-only.

    On first call against an empty table, seeds the default rubric so
    the operator does not have to type ten categories before using the matrix."""
    sess = get_session()
    if sess.scalar(select(SkillCategory).limit(1)) is None:
        seed_default_categories(sess)
    rows = sess.scalars(
        select(SkillCategory)
        .where(SkillCategory.active == 1)
        .order_by(SkillCategory.display_order.asc(), SkillCategory.name.asc())
    ).all()
    return jsonify([to_dict(r) for r in rows])


@bp.route("/api/v1/skills/categories", methods=["POST"])
@admin_required
def create_category():
    data = request.get_json(silent=True) or {}
    slug = (data.get("slug") or "").strip().lower()
    name = (data.get("name") or "").strip()
    if not slug or not name:
        return jsonify({"error": "slug and name are required",
                        "request_id": _rid()}), 400
    sess = get_session()
    if sess.scalar(select(SkillCategory).where(SkillCategory.slug == slug)) is not None:
        return jsonify({"error": "slug already exists",
                        "request_id": _rid()}), 409
    cat = SkillCategory(
        slug=slug,
        name=name,
        description=(data.get("description") or "").strip(),
        display_order=int(data.get("display_order") or 0),
        active=1,
    )
    sess.add(cat)
    sess.commit()
    return jsonify(to_dict(cat)), 201


@bp.route("/api/v1/skills/categories/<int:cat_id>", methods=["PATCH"])
@admin_required
def update_category(cat_id):
    sess = get_session()
    cat = sess.get(SkillCategory, cat_id)
    if cat is None:
        return jsonify({"error": "not found",
                        "request_id": _rid()}), 404
    data = request.get_json(silent=True) or {}
    for col in ("name", "description"):
        if col in data:
            val = (data[col] or "").strip()
            if col == "name" and not val:
                return jsonify({"error": "name cannot be blank",
                                "request_id": _rid()}), 400
            setattr(cat, col, val)
    if "display_order" in data:
        try:
            cat.display_order = int(data["display_order"])
        except (TypeError, ValueError):
            return jsonify({"error": "display_order must be an int",
                            "request_id": _rid()}), 400
    if "active" in data:
        cat.active = 1 if data["active"] else 0
    cat.updated_at = datetime.utcnow()
    sess.commit()
    return jsonify(to_dict(cat))


# ── Matrix ────────────────────────────────────────────────────────────────


@bp.route("/api/v1/skills/matrix", methods=["GET"])
@admin_required
def skill_matrix():
    """Single-fetch payload for the matrix UI.

    Shape:
        {
          "employees":  [{id, display_name, title, role, active}, ...],
          "categories": [{id, slug, name, description, display_order}, ...],
          "scores": {
            "<employee_id>": { "<category_id>": <score>, ... },
            ...
          }
        }

    Empty cells are simply absent from `scores[employee_id]` — the
    frontend renders them as a faint placeholder."""
    sess = get_session()
    # Seed defaults if needed so the first matrix request isn't blank.
    if sess.scalar(select(SkillCategory).limit(1)) is None:
        seed_default_categories(sess)
    include_inactive_emp = request.args.get("include_inactive_emp") in ("1", "true", "yes")
    include_untracked_emp = request.args.get("include_untracked_emp") in ("1", "true", "yes")

    cat_stmt = (
        select(SkillCategory)
        .where(SkillCategory.active == 1)
        .order_by(SkillCategory.display_order.asc(), SkillCategory.name.asc())
    )
    categories = [to_dict(c) for c in sess.scalars(cat_stmt).all()]

    emp_stmt = select(Employee).order_by(Employee.display_name.asc())
    if not include_inactive_emp:
        emp_stmt = emp_stmt.where(Employee.active == 1)
    if not include_untracked_emp:
        emp_stmt = emp_stmt.where(Employee.competency_tracked == 1)
    employees = [{
        "id": e.id,
        "display_name": e.display_name,
        "title": e.title,
        "role": e.role,
        "active": e.active,
        "competency_tracked": e.competency_tracked,
    } for e in sess.scalars(emp_stmt).all()]

    detail = request.args.get("detail") in ("1", "true", "yes")
    scores_by_emp: dict[str, dict[str, float | dict]] = {}
    score_rows = sess.scalars(select(EmployeeSkillScore)).all()
    for s in score_rows:
        if not detail:
            scores_by_emp.setdefault(str(s.employee_id), {})[str(s.category_id)] = s.score
            continue
        cell = detail_for_cell(sess, s.employee_id, s.category_id) or {
            "score": s.score,
            "confidence": s.confidence,
            "confidence_band": confidence_band(s.confidence),
            "sample_size": s.sample_size,
            "last_observed_at": s.last_observed_at.isoformat(sep=" ") if s.last_observed_at else "",
            "dimensions": [],
        }
        scores_by_emp.setdefault(str(s.employee_id), {})[str(s.category_id)] = cell

    payload = {
        "employees": employees,
        "categories": categories,
        "scores": scores_by_emp,
    }
    if detail:
        payload["dimensions"] = {
            str(c["id"]): [d.__dict__ for d in dimensions_for_category(sess.get(SkillCategory, c["id"]))]
            for c in categories
        }
    return jsonify(payload)


# ── Scores ────────────────────────────────────────────────────────────────


@bp.route("/api/v1/skills/scores", methods=["POST"])
@admin_required
def upsert_score_route():
    data = request.get_json(silent=True) or {}
    try:
        employee_id = int(data.get("employee_id"))
        category_id = int(data.get("category_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "employee_id + category_id (int) required",
                        "request_id": _rid()}), 400
    if "score" not in data:
        return jsonify({"error": "score is required",
                        "request_id": _rid()}), 400

    sess = get_session()
    if sess.get(Employee, employee_id) is None:
        return jsonify({"error": "employee not found",
                        "request_id": _rid()}), 404
    if sess.get(SkillCategory, category_id) is None:
        return jsonify({"error": "category not found",
                        "request_id": _rid()}), 404

    try:
        row = upsert_score(
            sess, employee_id, category_id, data["score"],
            notes=(data.get("notes") or "").strip(),
            source_kind=(data.get("source_kind") or "manual_override").strip(),
        )
    except CompetencyError as e:
        return jsonify({"error": str(e), "request_id": _rid()}), e.status_code
    sess.commit()
    return jsonify(to_dict(row))


@bp.route("/api/v1/skills/scores/<int:employee_id>", methods=["GET"])
@admin_required
def list_scores_for_employee(employee_id):
    sess = get_session()
    if sess.get(Employee, employee_id) is None:
        return jsonify({"error": "employee not found",
                        "request_id": _rid()}), 404
    rows = sess.scalars(
        select(EmployeeSkillScore)
        .where(EmployeeSkillScore.employee_id == employee_id)
    ).all()
    return jsonify([to_dict(r) for r in rows])


# ── Subscore evidence ─────────────────────────────────────────────────────


def _validate_emp_cat(sess, employee_id: int, category_id: int):
    if sess.get(Employee, employee_id) is None:
        return jsonify({"error": "employee not found", "request_id": _rid()}), 404
    if sess.get(SkillCategory, category_id) is None:
        return jsonify({"error": "category not found", "request_id": _rid()}), 404
    return None


@bp.route("/api/v1/skills/dimensions/<int:category_id>", methods=["GET"])
@admin_required
def list_dimensions(category_id):
    sess = get_session()
    category = sess.get(SkillCategory, category_id)
    if category is None:
        return jsonify({"error": "category not found", "request_id": _rid()}), 404
    return jsonify([d.__dict__ for d in dimensions_for_category(category)])


@bp.route("/api/v1/skills/subscores", methods=["POST"])
@admin_required
def create_subscore():
    data = request.get_json(silent=True) or {}
    try:
        employee_id = int(data.get("employee_id"))
        category_id = int(data.get("category_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "employee_id + category_id (int) required",
                        "request_id": _rid()}), 400
    if "score" not in data:
        return jsonify({"error": "score is required", "request_id": _rid()}), 400
    sess = get_session()
    validation = _validate_emp_cat(sess, employee_id, category_id)
    if validation is not None:
        return validation
    try:
        evidence, cached = add_subscore(
            sess,
            employee_id=employee_id,
            category_id=category_id,
            dimension_slug=(data.get("dimension_slug") or "").strip(),
            raw_score=data.get("score"),
            weight=data.get("weight"),
            observed_at=data.get("observed_at"),
            source_kind=(data.get("source_kind") or "manual").strip(),
            source_id=data.get("source_id"),
            notes=(data.get("notes") or "").strip(),
        )
    except CompetencyError as e:
        return jsonify({"error": str(e), "request_id": _rid()}), e.status_code
    sess.commit()
    return jsonify({"subscore": to_dict(evidence), "rollup": to_dict(cached)}), 201


@bp.route("/api/v1/skills/subscores/<int:employee_id>/<int:category_id>", methods=["GET"])
@admin_required
def list_subscores(employee_id, category_id):
    sess = get_session()
    validation = _validate_emp_cat(sess, employee_id, category_id)
    if validation is not None:
        return validation
    rows = sess.scalars(
        select(EmployeeSkillSubscore)
        .where(
            EmployeeSkillSubscore.employee_id == employee_id,
            EmployeeSkillSubscore.category_id == category_id,
        )
        .order_by(EmployeeSkillSubscore.observed_at.desc(), EmployeeSkillSubscore.id.desc())
    ).all()
    return jsonify({
        "rollup": detail_for_cell(sess, employee_id, category_id),
        "rows": [to_dict(r) for r in rows],
    })


@bp.route("/api/v1/skills/recompute", methods=["POST"])
@admin_required
def recompute_scores():
    sess = get_session()
    count = recompute_all(sess)
    sess.commit()
    return jsonify({"updated": count})
