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
from app.services.tickets import _coerce_fk_columns, enrich_with_fks, validate_record_data

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
    assert body["project_name"] == "Bridge"
    assert body["engineer"] == "Pat Person"
    # FK columns are populated by enrich_with_fks.
    assert body["project_id"] is not None
    assert body["engineer_id"] is not None


def test_project_task_registry_syncs_name_on_create_and_update(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="0042.00", name="Bridge"))
        sess.add(Project(project_number="0043.00", name="Library"))
        sess.commit()

    created = auth_client.post("/api/v1/project_work_tasks", json={
        "title": "Survey check",
        "project_number": "0042.00",
        "task_description": "Verify the survey baseline",
    })
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body["project_name"] == "Bridge"
    assert body["project_id"] is not None

    updated = auth_client.put(f"/api/v1/project_work_tasks/{body['id']}", json={
        "project_number": "0043.00",
    })
    assert updated.status_code == 200, updated.get_json()
    body = updated.get_json()
    assert body["project_number"] == "0043.00"
    assert body["project_name"] == "Library"
    assert body["project_id"] is not None


def test_project_task_execution_fields_roundtrip_and_validation(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="0044.00", name="Pump Station"))
        sess.commit()

    created = auth_client.post("/api/v1/project_work_tasks", json={
        "title": "Draft exhibit",
        "project_number": "0044.00",
        "task_description": "Prepare the exhibit package",
        "due_at": "2026-06-08T17:00",
        "scheduled_completion_at": "2026-06-08T14:30",
        "time_required_minutes": "90",
        "scope_notes": "Confirm exhibit scope before starting.",
        "progress_notes": "Base file is ready.",
        "confirmation_notes": "PM needs to confirm sheet list.",
        "completion_notes": "Deliverable goes to the agency folder.",
    })
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body["project_name"] == "Pump Station"
    assert body["scheduled_completion_at"] == "2026-06-08T14:30"
    assert body["time_required_minutes"] == 90
    assert body["scope_notes"] == "Confirm exhibit scope before starting."

    updated = auth_client.put(f"/api/v1/project_work_tasks/{body['id']}", json={
        "status": "Pending Confirmation",
        "confirmation_notes": "Engineer review requested.",
        "time_required_minutes": 120,
    })
    assert updated.status_code == 200, updated.get_json()
    updated_body = updated.get_json()
    assert updated_body["status"] == "Pending Confirmation"
    assert updated_body["confirmation_notes"] == "Engineer review requested."
    assert updated_body["time_required_minutes"] == 120

    bad_minutes = validate_record_data(
        "project_work_tasks",
        {"time_required_minutes": "45"},
    )
    assert bad_minutes == "Time Required must use 30-minute increments"

    bad_schedule = validate_record_data(
        "project_work_tasks",
        {"scheduled_completion_at": "not-a-date"},
    )
    assert bad_schedule == "Scheduled completion must be a valid datetime"
