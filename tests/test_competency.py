"""Tests for app/routes/competency.py — categories + matrix + score upserts.

Every endpoint is admin-only. Verifies seed-on-first-call, score clamping,
upsert idempotency, and activity_log writes."""
from sqlalchemy import select

from app.db import get_session
from app.models import ActivityLog, Employee, EmployeeSkillScore, SkillCategory

# ── Auth gating ───────────────────────────────────────────────────────────


def test_categories_requires_admin(auth_client):
    r = auth_client.get("/api/v1/skills/categories")
    assert r.status_code == 403


def test_matrix_requires_admin(auth_client):
    r = auth_client.get("/api/v1/skills/matrix")
    assert r.status_code == 403


def test_upsert_requires_admin(auth_client):
    r = auth_client.post("/api/v1/skills/scores", json={
        "employee_id": 1, "category_id": 1, "score": 5,
    })
    assert r.status_code == 403


def test_categories_anonymous_blocked(client):
    assert client.get("/api/v1/skills/categories").status_code == 401


# ── Default seeding ───────────────────────────────────────────────────────


def test_categories_seeds_defaults_on_first_call(admin_client, temp_app):
    """Empty table → first GET seeds the 10 default rubric categories."""
    with temp_app.app_context():
        sess = get_session()
        assert sess.scalar(select(SkillCategory)) is None

    r = admin_client.get("/api/v1/skills/categories")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 10
    slugs = {row["slug"] for row in rows}
    assert "project-setup" in slugs
    assert "cad-standards" in slugs
    assert "software-proficiency" in slugs


def test_categories_seed_is_idempotent(admin_client):
    admin_client.get("/api/v1/skills/categories")
    first = admin_client.get("/api/v1/skills/categories").get_json()
    second = admin_client.get("/api/v1/skills/categories").get_json()
    assert len(first) == len(second) == 10


# ── Category CRUD ─────────────────────────────────────────────────────────


def test_create_custom_category(admin_client):
    admin_client.get("/api/v1/skills/categories")  # seed
    r = admin_client.post("/api/v1/skills/categories", json={
        "slug": "br-special", "name": "BR Special Sauce",
        "description": "Internal BR rubric extension.",
        "display_order": 200,
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["slug"] == "br-special"


def test_create_category_rejects_duplicate_slug(admin_client):
    admin_client.get("/api/v1/skills/categories")
    r = admin_client.post("/api/v1/skills/categories", json={
        "slug": "cad-standards", "name": "Dup",
    })
    assert r.status_code == 409


def test_create_category_requires_slug_and_name(admin_client):
    r = admin_client.post("/api/v1/skills/categories", json={"name": "x"})
    assert r.status_code == 400


def test_patch_category(admin_client):
    rows = admin_client.get("/api/v1/skills/categories").get_json()
    cat_id = next(r["id"] for r in rows if r["slug"] == "permitting")
    r = admin_client.patch(f"/api/v1/skills/categories/{cat_id}", json={
        "name": "Permitting (renamed)",
        "display_order": 75,
    })
    assert r.status_code == 200
    assert r.get_json()["name"] == "Permitting (renamed)"


def test_categories_filter_inactive(admin_client):
    rows = admin_client.get("/api/v1/skills/categories").get_json()
    cat_id = next(r["id"] for r in rows if r["slug"] == "permitting")
    admin_client.patch(f"/api/v1/skills/categories/{cat_id}", json={"active": False})

    # Default GET hides inactive
    after = admin_client.get("/api/v1/skills/categories").get_json()
    slugs = {row["slug"] for row in after}
    assert "permitting" not in slugs


# ── Matrix payload ────────────────────────────────────────────────────────


def test_matrix_payload_shape(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="Test Person", role="engineer"))
        sess.commit()

    r = admin_client.get("/api/v1/skills/matrix")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body.keys()) == {"employees", "categories", "scores"}
    assert isinstance(body["employees"], list)
    assert isinstance(body["categories"], list)
    assert isinstance(body["scores"], dict)
    assert any(e["display_name"] == "Test Person" for e in body["employees"])


# ── Score upsert ──────────────────────────────────────────────────────────


def _seed_pair(temp_app):
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name="Scoring Subject", role="engineer")
        cat = SkillCategory(slug="test-cat", name="Test Category")
        sess.add(emp)
        sess.add(cat)
        sess.commit()
        return emp.id, cat.id


def test_upsert_score_insert(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 7.5,
    })
    assert r.status_code == 200
    assert r.get_json()["score"] == 7.5

    with temp_app.app_context():
        sess = get_session()
        rows = sess.scalars(
            select(EmployeeSkillScore).where(EmployeeSkillScore.employee_id == emp_id)
        ).all()
        assert len(rows) == 1


def test_upsert_score_update(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 5.0,
    })
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 8.0,
    })
    assert r.status_code == 200
    assert r.get_json()["score"] == 8.0

    with temp_app.app_context():
        sess = get_session()
        rows = sess.scalars(
            select(EmployeeSkillScore).where(EmployeeSkillScore.employee_id == emp_id)
        ).all()
        # Still one row — upsert, not insert-twice.
        assert len(rows) == 1
        assert rows[0].score == 8.0


def test_upsert_writes_activity_log(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 6,
    })
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 7.5,
    })
    with temp_app.app_context():
        sess = get_session()
        rows = sess.scalars(
            select(ActivityLog).where(ActivityLog.table_name == "employee_skill_scores")
        ).all()
    actions = [r.action for r in rows]
    assert "score_set" in actions
    assert "score_updated" in actions


def test_upsert_clamps_too_high(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 11,
    })
    assert r.status_code == 400


def test_upsert_clamps_too_low(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 0,
    })
    assert r.status_code == 400


def test_upsert_rejects_bad_score(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": "not-a-number",
    })
    assert r.status_code == 400


def test_upsert_404_missing_employee(admin_client):
    admin_client.get("/api/v1/skills/categories")  # seed cats
    cats = admin_client.get("/api/v1/skills/categories").get_json()
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": 99999, "category_id": cats[0]["id"], "score": 5,
    })
    assert r.status_code == 404


def test_upsert_404_missing_category(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="No Cat"))
        sess.commit()
    rows = admin_client.get("/api/v1/employees").get_json()
    emp_id = next(r["id"] for r in rows if r["display_name"] == "No Cat")

    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": 99999, "score": 5,
    })
    assert r.status_code == 404


# ── Per-employee score list ───────────────────────────────────────────────


def test_list_scores_for_employee(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 6.5,
    })
    r = admin_client.get(f"/api/v1/skills/scores/{emp_id}")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["score"] == 6.5


def test_list_scores_404_missing_employee(admin_client):
    r = admin_client.get("/api/v1/skills/scores/99999")
    assert r.status_code == 404
