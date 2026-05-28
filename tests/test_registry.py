"""Tests for app/routes/registry.py — Employees + Projects CRUD.

All endpoints are admin-only. Covers create / list / get / patch /
deactivate / reactivate for both. Negative paths: anonymous → 401,
regular user → 403, duplicate project_number → 409.
"""
from app.db import get_session
from app.models import Employee, Project, ProjectOverlay

# ── Auth gating ───────────────────────────────────────────────────────────


def test_anonymous_blocked(client):
    assert client.get("/api/v1/employees").status_code == 401
    assert client.get("/api/v1/projects").status_code == 401
    assert client.post("/api/v1/employees", json={"display_name": "x"}).status_code == 401


def test_regular_user_forbidden(auth_client):
    assert auth_client.get("/api/v1/employees").status_code == 403
    assert auth_client.post("/api/v1/projects", json={"project_number": "1234.56"}).status_code == 403


# ── Employees ─────────────────────────────────────────────────────────────


def test_create_employee(admin_client, temp_app):
    r = admin_client.post("/api/v1/employees", json={
        "display_name": "Jane Engineer",
        "title": "Senior Drafter",
        "email": "jane@example.com",
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["display_name"] == "Jane Engineer"
    assert body["title"] == "Senior Drafter"
    assert body["active"] == 1
    assert body["id"] > 0

    with temp_app.app_context():
        sess = get_session()
        emp = sess.get(Employee, body["id"])
        assert emp is not None and emp.email == "jane@example.com"


def test_create_employee_rejects_blank(admin_client):
    r = admin_client.post("/api/v1/employees", json={"display_name": "   "})
    assert r.status_code == 400


def test_list_employees_filters_active(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="Active One", active=1))
        sess.add(Employee(display_name="Disabled One", active=0))
        sess.commit()

    r = admin_client.get("/api/v1/employees")
    names = {row["display_name"] for row in r.get_json()}
    assert "Active One" in names
    assert "Disabled One" not in names

    r2 = admin_client.get("/api/v1/employees?include_inactive=1")
    names2 = {row["display_name"] for row in r2.get_json()}
    assert "Disabled One" in names2


def test_patch_employee(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name="Patch Target", title="Old")
        sess.add(emp)
        sess.commit()
        emp_id = emp.id

    r = admin_client.patch(f"/api/v1/employees/{emp_id}", json={"title": "New title"})
    assert r.status_code == 200
    assert r.get_json()["title"] == "New title"


def test_deactivate_then_reactivate(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name="Cycle Me")
        sess.add(emp)
        sess.commit()
        emp_id = emp.id

    r = admin_client.delete(f"/api/v1/employees/{emp_id}")
    assert r.status_code == 200
    assert r.get_json() == {"deactivated": emp_id}

    with temp_app.app_context():
        sess = get_session()
        assert sess.get(Employee, emp_id).active == 0

    r2 = admin_client.patch(f"/api/v1/employees/{emp_id}", json={"active": True})
    assert r2.status_code == 200
    assert r2.get_json()["active"] == 1


# ── Projects ──────────────────────────────────────────────────────────────


def test_create_project(admin_client, temp_app):
    r = admin_client.post("/api/v1/projects", json={
        "project_number": "1234.56",
        "name": "Test bridge",
        "client": "City of Foo",
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["project_number"] == "1234.56"

    with temp_app.app_context():
        sess = get_session()
        assert sess.get(Project, body["id"]) is not None


def test_duplicate_project_number_rejected(admin_client, temp_app):
    admin_client.post("/api/v1/projects", json={"project_number": "9999.99"})
    r = admin_client.post("/api/v1/projects", json={"project_number": "9999.99"})
    assert r.status_code == 409
    assert "existing_id" in r.get_json()


def test_create_project_rejects_blank_number(admin_client):
    r = admin_client.post("/api/v1/projects", json={"project_number": "   "})
    assert r.status_code == 400


def test_list_projects_sorted_by_number(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="0002.00", name="Two"))
        sess.add(Project(project_number="0001.00", name="One"))
        sess.commit()

    r = admin_client.get("/api/v1/projects")
    numbers = [row["project_number"] for row in r.get_json()]
    assert numbers == sorted(numbers)

def test_project_overlay_api_is_tasktrack_owned(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj = Project(project_number="4466.77", name="Overlay project")
        sess.add(proj)
        sess.commit()
        proj_id = proj.id

    r = auth_client.get(f"/api/v1/projects/{proj_id}/overlay")
    assert r.status_code == 200
    body = r.get_json()
    assert body["project_number"] == "4466.77"
    assert body["operator_status"] == ""

    denied = auth_client.patch(f"/api/v1/projects/{proj_id}/overlay", json={"operator_status": "Watch"})
    assert denied.status_code == 403

    with auth_client.session_transaction() as session_data:
        session_data["user_id"] = 2
        session_data["user_name"] = "Admin User"
        session_data["user_role"] = "admin"

    r = auth_client.patch(f"/api/v1/projects/{proj_id}/overlay", json={
        "operator_status": "Watch",
        "priority": "High",
        "tags": "meeting, budget",
        "next_review_date": "2026-06-01",
        "internal_notes": "Private operator context",
        "report_note": "Discuss at management sync",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["operator_status"] == "Watch"
    assert body["priority"] == "High"
    assert body["internal_notes"] == "Private operator context"

    with auth_client.session_transaction() as session_data:
        session_data["user_id"] = 1
        session_data["user_name"] = "Tester"
        session_data["user_role"] = "user"

    r = auth_client.get(f"/api/v1/projects/{proj_id}/overlay")
    assert r.status_code == 200
    body = r.get_json()
    assert body["report_note"] == "Discuss at management sync"
    assert body["internal_notes"] == ""
    assert "Private operator context" not in str(body)

    with auth_client.session_transaction() as session_data:
        session_data["user_id"] = 2
        session_data["user_name"] = "Admin User"
        session_data["user_role"] = "admin"

    r = auth_client.get(f"/api/v1/projects/{proj_id}/overlay")
    assert r.status_code == 200
    assert r.get_json()["internal_notes"] == "Private operator context"

    with temp_app.app_context():
        sess = get_session()
        row = sess.query(ProjectOverlay).filter_by(project_id=proj_id).one()
        assert row.report_note == "Discuss at management sync"


def test_overlay_get_does_not_attach_stranded_overlay(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj = Project(project_number="4477.88", name="Current project")
        sess.add(proj)
        sess.flush()
        sess.add(ProjectOverlay(
            project_id=999999,
            project_number="4477.88",
            operator_status="Old project context",
            internal_notes="Old internal context",
        ))
        sess.commit()
        proj_id = proj.id

    r = auth_client.get(f"/api/v1/projects/{proj_id}/overlay")
    assert r.status_code == 200
    body = r.get_json()
    assert body["project_id"] == proj_id
    assert body["operator_status"] == ""
    assert "Old internal context" not in str(body)

    with temp_app.app_context():
        sess = get_session()
        row = sess.query(ProjectOverlay).filter_by(project_number="4477.88").one()
        assert row.project_id == 999999


def test_overlay_patch_conflicts_on_stranded_overlay(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj = Project(project_number="4488.99", name="Current project")
        sess.add(proj)
        sess.flush()
        sess.add(ProjectOverlay(
            project_id=999999,
            project_number="4488.99",
            operator_status="Old project context",
        ))
        sess.commit()
        proj_id = proj.id

    with auth_client.session_transaction() as session_data:
        session_data["user_id"] = 2
        session_data["user_name"] = "Admin User"
        session_data["user_role"] = "admin"

    r = auth_client.patch(
        f"/api/v1/projects/{proj_id}/overlay",
        json={"operator_status": "New project context"},
    )
    assert r.status_code == 409
    assert "overlay" in r.get_json()["error"]

    with temp_app.app_context():
        sess = get_session()
        row = sess.query(ProjectOverlay).filter_by(project_number="4488.99").one()
        assert row.project_id == 999999
        assert row.operator_status == "Old project context"


def test_admin_projects_page_links_to_workspace_map_and_reports(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="4455.66", name="Linked admin project"))
        sess.commit()

    r = admin_client.get("/admin/projects")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '/?workspace=4455.66' in html
    assert '/?map_project=4455.66' in html
    assert '/reports/project?project_number=4455.66' in html
