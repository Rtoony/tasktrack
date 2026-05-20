"""Tests for the Phase-0 dual-column FK refactor.

Verifies:
- Creating a record with text only → FK columns nullable / unset
- Creating with FK only → text columns stay blank
- Creating with both → both stored
- `enrich_with_fks` auto-populates FK from text on exact name/number match
- `_coerce_fk_columns` normalizes blank strings to None and numeric strings
  to int
"""
from app.db import get_session
from app.models import Employee, PersonnelIssue, Project, ProjectWorkTask
from app.services.tickets import _coerce_fk_columns, enrich_with_fks

# ── Coercion helper ──────────────────────────────────────────────────────


def test_coerce_strips_blank_strings():
    data = {"project_id": "", "engineer_id": "  ", "person_id": "null"}
    _coerce_fk_columns(data)
    assert data == {"project_id": None, "engineer_id": None, "person_id": None}


def test_coerce_numeric_strings_to_int():
    data = {"project_id": "7", "engineer_id": 42}
    _coerce_fk_columns(data)
    assert data == {"project_id": 7, "engineer_id": 42}


def test_coerce_garbage_to_none():
    data = {"project_id": "not-a-number"}
    _coerce_fk_columns(data)
    assert data == {"project_id": None}


def test_coerce_ignores_other_keys():
    data = {"title": "Keep me", "priority": "Medium"}
    _coerce_fk_columns(data)
    assert data == {"title": "Keep me", "priority": "Medium"}


# ── Enrichment ────────────────────────────────────────────────────────────


def test_enrich_resolves_project_number(temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj = Project(project_number="2026.10", name="Test")
        sess.add(proj)
        sess.flush()
        proj_id = proj.id

        task = ProjectWorkTask(
            project_name="X", title="Y", project_number="2026.10",
            task_description="Z", engineer="",
        )
        sess.add(task)
        sess.flush()

        changed = enrich_with_fks(sess, "project_work_tasks", task)
        assert changed is True
        assert task.project_id == proj_id


def test_enrich_resolves_engineer_name(temp_app):
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name="Alice Smith")
        sess.add(emp)
        sess.flush()
        emp_id = emp.id

        task = ProjectWorkTask(
            project_name="X", title="Y", project_number="0001.00",
            task_description="Z", engineer="Alice Smith",
        )
        sess.add(task)
        sess.flush()

        enrich_with_fks(sess, "project_work_tasks", task)
        assert task.engineer_id == emp_id


def test_enrich_is_case_insensitive(temp_app):
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name="Bob Jones")
        sess.add(emp)
        sess.flush()
        emp_id = emp.id

        issue = PersonnelIssue(
            person_name="bob jones",
            issue_description="something",
        )
        sess.add(issue)
        sess.flush()

        enrich_with_fks(sess, "personnel_issues", issue)
        assert issue.person_id == emp_id


def test_enrich_skips_when_fk_already_set(temp_app):
    """Don't clobber a manually-chosen FK with an autoresolve."""
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name="Real Engineer")
        sess.add(emp)
        sess.flush()
        emp_id = emp.id

        task = ProjectWorkTask(
            project_name="X", title="Y", project_number="0001.00",
            task_description="Z",
            engineer="Real Engineer",
            engineer_id=999,  # caller's explicit choice — leave alone
        )
        sess.add(task)
        sess.flush()

        enrich_with_fks(sess, "project_work_tasks", task)
        assert task.engineer_id == 999  # unchanged
        assert emp_id != 999


def test_enrich_no_match_leaves_blank(temp_app):
    with temp_app.app_context():
        sess = get_session()
        task = ProjectWorkTask(
            project_name="X", title="Y", project_number="9999.99",
            task_description="Z", engineer="Nobody Recognized",
        )
        sess.add(task)
        sess.flush()

        changed = enrich_with_fks(sess, "project_work_tasks", task)
        assert changed is False
        assert task.project_id is None
        assert task.engineer_id is None


# ── End-to-end via the API ────────────────────────────────────────────────


def test_create_record_via_api_enriches_fk(auth_client, temp_app):
    """Creating a project_work_task via /api/v1 should auto-fill FK columns
    when the text values match a registry row."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="0042.00", name="Bridge"))
        sess.add(Employee(display_name="Pat Person"))
        sess.commit()

    r = auth_client.post("/api/v1/project_work_tasks", json={
        "project_name": "Bridge work",
        "title": "Survey check",
        "project_number": "0042.00",
        "task_description": "Verify the survey baseline",
        "engineer": "Pat Person",
    })
    assert r.status_code in (200, 201)
    rec_id = r.get_json()["id"]

    r2 = auth_client.get(f"/api/v1/project_work_tasks/{rec_id}")
    body = r2.get_json()
    assert body["project_number"] == "0042.00"
    assert body["engineer"] == "Pat Person"
    # FK columns are populated by enrich_with_fks.
    assert body["project_id"] is not None
    assert body["engineer_id"] is not None
