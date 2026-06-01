"""Tests for app/routes/competency.py — categories + competency rollups.

Every endpoint is admin-only. Verifies seed-on-first-call, 0-3 score validation,
evidence rows, cached rollups, detail payloads, and activity_log writes."""
from sqlalchemy import select

from app.db import get_session
from app.models import ActivityLog, Employee, EmployeeSkillScore, EmployeeSkillSubscore, SkillCategory

# ── Auth gating ───────────────────────────────────────────────────────────


def test_categories_get_open_to_logged_in_users(auth_client):
    """Phase-2 opened GET on categories so the personnel fk-select widget
    can populate for non-admin users. Mutations (POST/PATCH) stay
    admin-only — covered by separate tests below."""
    r = auth_client.get("/api/v1/skills/categories")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)


def test_categories_post_still_admin_only(auth_client):
    r = auth_client.post("/api/v1/skills/categories", json={
        "slug": "x", "name": "Y",
    })
    assert r.status_code == 403


def test_matrix_requires_admin(auth_client):
    r = auth_client.get("/api/v1/skills/matrix")
    assert r.status_code == 403


def test_upsert_requires_admin(auth_client):
    r = auth_client.post("/api/v1/skills/scores", json={
        "employee_id": 1, "category_id": 1, "score": 3,
    })
    assert r.status_code == 403


def test_categories_anonymous_blocked(client):
    assert client.get("/api/v1/skills/categories").status_code == 401


# ── Default seeding ───────────────────────────────────────────────────────


def test_categories_seeds_defaults_on_first_call(admin_client, temp_app):
    """Empty table -> first GET seeds the 6 CAD/GIS v2 categories."""
    with temp_app.app_context():
        sess = get_session()
        assert sess.scalar(select(SkillCategory)) is None

    r = admin_client.get("/api/v1/skills/categories")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 6
    slugs = {row["slug"] for row in rows}
    assert "computer-windows-literacy" in slugs
    assert "autocad-core" in slugs
    assert "civil-3d" in slugs


def test_categories_seed_is_idempotent(admin_client):
    admin_client.get("/api/v1/skills/categories")
    first = admin_client.get("/api/v1/skills/categories").get_json()
    second = admin_client.get("/api/v1/skills/categories").get_json()
    assert len(first) == len(second) == 6


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
        "slug": "autocad-core", "name": "Dup",
    })
    assert r.status_code == 409


def test_create_category_requires_slug_and_name(admin_client):
    r = admin_client.post("/api/v1/skills/categories", json={"name": "x"})
    assert r.status_code == 400


def test_patch_category(admin_client):
    rows = admin_client.get("/api/v1/skills/categories").get_json()
    cat_id = next(r["id"] for r in rows if r["slug"] == "civil-3d")
    r = admin_client.patch(f"/api/v1/skills/categories/{cat_id}", json={
        "name": "Civil 3D (renamed)",
        "display_order": 75,
    })
    assert r.status_code == 200
    assert r.get_json()["name"] == "Civil 3D (renamed)"


def test_categories_filter_inactive(admin_client):
    rows = admin_client.get("/api/v1/skills/categories").get_json()
    cat_id = next(r["id"] for r in rows if r["slug"] == "civil-3d")
    admin_client.patch(f"/api/v1/skills/categories/{cat_id}", json={"active": False})

    # Default GET hides inactive
    after = admin_client.get("/api/v1/skills/categories").get_json()
    slugs = {row["slug"] for row in after}
    assert "civil-3d" not in slugs


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


def test_matrix_hides_untracked_employees_by_default(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="Rated Person", active=1, competency_tracked=1))
        sess.add(Employee(display_name="Excluded Person", active=1, competency_tracked=0))
        sess.commit()

    r = admin_client.get("/api/v1/skills/matrix")
    assert r.status_code == 200
    names = {e["display_name"] for e in r.get_json()["employees"]}
    assert "Rated Person" in names
    assert "Excluded Person" not in names

    r2 = admin_client.get("/api/v1/skills/matrix?include_untracked_emp=1")
    assert r2.status_code == 200
    names2 = {e["display_name"] for e in r2.get_json()["employees"]}
    assert "Rated Person" in names2
    assert "Excluded Person" in names2


def test_upsert_score_insert(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 2,
    })
    assert r.status_code == 200
    assert r.get_json()["score"] == 2.0

    with temp_app.app_context():
        sess = get_session()
        rows = sess.scalars(
            select(EmployeeSkillScore).where(EmployeeSkillScore.employee_id == emp_id)
        ).all()
        assert len(rows) == 1


def test_upsert_score_update(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 1,
    })
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 3,
    })
    assert r.status_code == 200
    assert r.get_json()["score"] == 3.0

    with temp_app.app_context():
        sess = get_session()
        rows = sess.scalars(
            select(EmployeeSkillScore).where(EmployeeSkillScore.employee_id == emp_id)
        ).all()
        # Still one row — upsert, not insert-twice.
        assert len(rows) == 1
        assert rows[0].score == 3.0


def test_upsert_writes_activity_log(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 3,
    })
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 2,
    })
    with temp_app.app_context():
        sess = get_session()
        rows = sess.scalars(
            select(ActivityLog).where(ActivityLog.table_name == "employee_skill_scores")
        ).all()
    actions = [r.action for r in rows]
    assert "score_set" in actions
    assert "score_updated" in actions


def test_upsert_rejects_too_high(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 4,
    })
    assert r.status_code == 400


def test_upsert_rejects_too_low(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": -1,
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
        "employee_id": 99999, "category_id": cats[0]["id"], "score": 3,
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
        "employee_id": emp_id, "category_id": 99999, "score": 3,
    })
    assert r.status_code == 404


# ── Per-employee score list ───────────────────────────────────────────────


def test_bulk_upsert_scores_sets_employee_scorecard(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name="Scorecard Subject", role="engineer")
        cat1 = SkillCategory(slug="scorecard-one", name="Scorecard One")
        cat2 = SkillCategory(slug="scorecard-two", name="Scorecard Two")
        sess.add_all([emp, cat1, cat2])
        sess.commit()
        emp_id = emp.id
        cat1_id = cat1.id
        cat2_id = cat2.id

    r = admin_client.post("/api/v1/skills/scores/bulk", json={
        "employee_id": emp_id,
        "source_kind": "preliminary_rating",
        "ratings": [
            {"category_id": cat1_id, "score": 2, "notes": "first pass"},
            {"category_id": cat2_id, "score": 3, "notes": "strong"},
        ],
    })
    assert r.status_code == 200
    assert r.get_json()["updated"] == 2

    matrix = admin_client.get("/api/v1/skills/matrix?detail=1").get_json()
    cell1 = matrix["scores"][str(emp_id)][str(cat1_id)]
    cell2 = matrix["scores"][str(emp_id)][str(cat2_id)]
    assert cell1["score"] == 2.0
    assert cell1["latest_preliminary"]["notes"] == "first pass"
    assert cell1["latest_preliminary"]["created_by_name"] == "Admin User"
    assert cell2["score"] == 3.0
    assert cell2["latest_preliminary"]["notes"] == "strong"


def test_list_scores_for_employee(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 2,
    })
    r = admin_client.get(f"/api/v1/skills/scores/{emp_id}")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["score"] == 2.0


def test_list_scores_404_missing_employee(admin_client):
    r = admin_client.get("/api/v1/skills/scores/99999")
    assert r.status_code == 404


# ── Subscore evidence + detail payload ────────────────────────────────────


def test_upsert_score_creates_manual_subscore(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 3,
        "notes": "forced value",
    })
    assert r.status_code == 200
    with temp_app.app_context():
        sess = get_session()
        evidence = sess.scalars(select(EmployeeSkillSubscore)).all()
        assert len(evidence) == 1
        assert evidence[0].dimension_slug == "manual"
        assert evidence[0].source_kind == "manual_override"
        score = sess.scalar(select(EmployeeSkillScore))
        assert score.score == 3.0
        assert score.sample_size == 1
        assert score.confidence >= 0.3


def test_upsert_score_accepts_rating_phase_source(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 2,
        "source_kind": "preliminary_rating",
        "notes": "initial pass",
    })
    assert r.status_code == 200
    r2 = admin_client.post("/api/v1/skills/scores", json={
        "employee_id": emp_id, "category_id": cat_id, "score": 3,
        "source_kind": "official_baseline",
        "notes": "approved baseline",
    })
    assert r2.status_code == 200

    with temp_app.app_context():
        sess = get_session()
        sources = [
            row.source_kind
            for row in sess.scalars(
                select(EmployeeSkillSubscore)
                .where(EmployeeSkillSubscore.employee_id == emp_id)
                .order_by(EmployeeSkillSubscore.id.asc())
            ).all()
        ]
        assert sources == ["preliminary_rating", "official_baseline"]
        rollup = sess.scalar(select(EmployeeSkillScore).where(EmployeeSkillScore.employee_id == emp_id))
        assert rollup.score == 3.0

    matrix = admin_client.get("/api/v1/skills/matrix?detail=1").get_json()
    cell = matrix["scores"][str(emp_id)][str(cat_id)]
    assert cell["latest_preliminary"]["score"] == 2.0
    assert cell["latest_preliminary"]["created_by_name"] == "Admin User"
    assert cell["latest_baseline"]["score"] == 3.0
    assert cell["latest_baseline"]["created_by_name"] == "Admin User"

    history = admin_client.get(f"/api/v1/skills/subscores/{emp_id}/{cat_id}").get_json()
    assert history["rating_markers"]["latest_preliminary"]["created_by_name"] == "Admin User"
    assert all(row["created_by_name"] == "Admin User" for row in history["rows"])


def test_create_subscore_appends_and_rolls_up(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r1 = admin_client.post("/api/v1/skills/subscores", json={
        "employee_id": emp_id, "category_id": cat_id,
        "dimension_slug": "observed-readiness", "score": 3,
    })
    r2 = admin_client.post("/api/v1/skills/subscores", json={
        "employee_id": emp_id, "category_id": cat_id,
        "dimension_slug": "observed-readiness", "score": 2,
    })
    assert r1.status_code == 201
    assert r2.status_code == 201
    with temp_app.app_context():
        sess = get_session()
        rows = sess.scalars(select(EmployeeSkillSubscore)).all()
        assert len(rows) == 2
        rollup = sess.scalar(select(EmployeeSkillScore))
        assert 2.0 <= rollup.score <= 3.0
        assert rollup.sample_size == 2
        assert rollup.rollup_version == 2


def test_matrix_detail_shape(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/subscores", json={
        "employee_id": emp_id, "category_id": cat_id,
        "dimension_slug": "observed-readiness", "score": 3,
    })
    r = admin_client.get("/api/v1/skills/matrix?detail=1")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body.keys()) == {"employees", "categories", "scores", "dimensions", "levels"}
    cell = body["scores"][str(emp_id)][str(cat_id)]
    assert cell["score"] == 3.0
    assert cell["confidence_band"] in {"low", "medium", "high"}
    assert cell["sample_size"] == 1
    assert cell["dimensions"][0]["slug"] == "observed-readiness"


def test_list_subscores_history(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/subscores", json={
        "employee_id": emp_id, "category_id": cat_id,
        "dimension_slug": "observed-readiness", "score": 3,
    })
    r = admin_client.get(f"/api/v1/skills/subscores/{emp_id}/{cat_id}")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["rows"]) == 1
    assert body["rollup"]["score"] == 3.0


def test_self_assessment_does_not_change_observed_rollup(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post("/api/v1/skills/subscores", json={
        "employee_id": emp_id, "category_id": cat_id,
        "dimension_slug": "observed-readiness", "score": 3,
        "source_kind": "self_assessment",
    })
    assert r.status_code == 201
    assert r.get_json()["rollup"] is None

    matrix = admin_client.get("/api/v1/skills/matrix?detail=1").get_json()
    cell = matrix["scores"][str(emp_id)][str(cat_id)]
    assert cell["score"] is None
    assert cell["task_markers"]["observed-readiness"]["latest_self"]["score"] == 3.0

    admin_client.post("/api/v1/skills/subscores", json={
        "employee_id": emp_id, "category_id": cat_id,
        "dimension_slug": "observed-readiness", "score": 2,
        "source_kind": "preliminary_rating",
    })
    matrix = admin_client.get("/api/v1/skills/matrix?detail=1").get_json()
    cell = matrix["scores"][str(emp_id)][str(cat_id)]
    assert cell["score"] == 2.0
    assert cell["task_markers"]["observed-readiness"]["latest_self"]["score"] == 3.0


def test_recompute_scores(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    admin_client.post("/api/v1/skills/subscores", json={
        "employee_id": emp_id, "category_id": cat_id,
        "dimension_slug": "observed-readiness", "score": 3,
    })
    r = admin_client.post("/api/v1/skills/recompute", json={})
    assert r.status_code == 200
    assert r.get_json()["updated"] == 1
