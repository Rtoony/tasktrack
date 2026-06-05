"""Phase-2: additive columns on personnel_issues.

Verifies the three new columns (estimated_time_loss_minutes,
immediate_solution, skill_category_id) round-trip through the generic
CRUD path and show up in to_dict output.
"""
from sqlalchemy import select

from app.db import get_session
from app.models import PersonnelIssue, SkillCategory


def test_create_with_time_loss_and_solution(auth_client):
    r = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Some Person",
        "issue_description": "Did a thing wrong",
        "estimated_time_loss_minutes": 90,
        "immediate_solution": "Took over the deliverable myself",
    })
    assert r.status_code in (200, 201)
    rec = r.get_json()
    assert rec["estimated_time_loss_minutes"] == 90
    assert rec["immediate_solution"] == "Took over the deliverable myself"


def test_create_defaults_when_fields_omitted(auth_client):
    """All three new columns are nullable / defaulted; omitting them
    must not break the existing intake path."""
    r = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Quiet One",
        "issue_description": "Nothing fancy",
    })
    assert r.status_code in (200, 201)
    rec = r.get_json()
    assert rec["estimated_time_loss_minutes"] == 0
    assert rec["immediate_solution"] == ""
    assert rec["skill_category_id"] is None


def test_skill_category_fk_persists(auth_client, temp_app):
    """The new FK column accepts an integer category id and survives
    a read-after-write."""
    with temp_app.app_context():
        sess = get_session()
        cat = SkillCategory(slug="t-cat", name="Test Cat")
        sess.add(cat)
        sess.commit()
        cat_id = cat.id

    r = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "FK Subject",
        "issue_description": "Needs the rubric link.",
        "skill_category_id": cat_id,
    })
    assert r.status_code in (200, 201)
    rec_id = r.get_json()["id"]

    r2 = auth_client.get(f"/api/v1/personnel_issues/{rec_id}")
    assert r2.get_json()["skill_category_id"] == cat_id


def test_patch_updates_new_columns(auth_client):
    create = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Patch Me",
        "issue_description": "Will be edited",
    }).get_json()
    rid = create["id"]

    patch = auth_client.put(f"/api/v1/personnel_issues/{rid}", json={
        "estimated_time_loss_minutes": 45,
        "immediate_solution": "Pair-coded the fix",
    })
    assert patch.status_code == 200
    body = patch.get_json()
    assert body["estimated_time_loss_minutes"] == 45
    assert body["immediate_solution"] == "Pair-coded the fix"



def test_dashboard_uses_controlled_cad_skill_area_dropdown(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert html.count("key:'cad_skill_area',label:'CAD Skill Area',type:'endpoint-select'") == 2
    assert "endpoint:'/api/v1/options/cad_skill_area'" in html
    assert "syncIdKey:'skill_category_id'" in html
    assert "syncIdSource:'skill_category_id'" in html
    assert "f.type==='endpoint-select'" in html
    assert "Current value: " in html

def test_model_columns_present(temp_app):
    """Sanity check the ORM model picks up the new columns — guards
    against future drift between models.py and the migration."""
    cols = {c.name for c in PersonnelIssue.__table__.columns}
    assert {"estimated_time_loss_minutes", "immediate_solution",
            "skill_category_id"}.issubset(cols)


def test_row_count_query_works(temp_app):
    """Smoke: select using the new column won't blow up because the
    column exists in the test DB schema (via Base.metadata.create_all)."""
    with temp_app.app_context():
        sess = get_session()
        rows = sess.scalars(
            select(PersonnelIssue).where(
                PersonnelIssue.estimated_time_loss_minutes > 0
            )
        ).all()
        assert rows == []
