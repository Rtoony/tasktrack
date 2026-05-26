"""Project status report MVP tests."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import CalendarEvent, Project, ProjectSite, ProjectWorkTask, WorkTask


def _seed_report_project(sess):
    proj = Project(
        project_number="7711.20",
        name="Report project",
        client="Acme Water",
        component="Site Improvement Plans",
        principal="Long, David",
        external_system="nexus-projects",
        external_ref="np-7711-20",
        lat=38.1,
        lng=-122.1,
    )
    sess.add(proj)
    sess.flush()
    sess.add(ProjectSite(project_id=proj.id, lat=38.1, lng=-122.1, pin_color="yellow", is_primary=1))
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    sess.add(ProjectWorkTask(
        project_name="Report project",
        title="Late exhibit",
        project_number="7711.20",
        task_description="Prepare exhibit",
        project_id=proj.id,
        status="In Progress",
        due_at=yesterday + "T17:00",
    ))
    sess.add(WorkTask(
        title="CAD standards check",
        project_number="7711.20",
        project_id=proj.id,
        status="In Progress",
    ))
    start = (datetime.now() + timedelta(days=2)).isoformat(timespec="minutes")
    sess.add(CalendarEvent(
        title="Public project review",
        event_type="review",
        start_at=start,
        project_number="7711.20",
        project_id=proj.id,
        visibility="internal",
        created_by_user_id=1,
    ))
    sess.add(CalendarEvent(
        title="Private project prep",
        event_type="prep",
        start_at=start,
        project_number="7711.20",
        project_id=proj.id,
        visibility="private",
        created_by_user_id=1,
    ))
    sess.commit()
    return proj.id


def test_project_report_json_summary_and_privacy(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_report_project(sess)

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    r = auth_client.get("/api/v1/reports/project?project_number=7711.20")
    assert r.status_code == 200
    body = r.get_json()
    assert body["project"]["project_number"] == "7711.20"
    assert body["counts"]["sites"] == 1
    assert body["counts"]["calendar_events"] == 1
    assert body["open_counts"]["project_work_tasks"] == 1
    assert [item["title"] for item in body["overdue_items"]] == ["Late exhibit"]
    event_titles = {event["title"] for event in body["upcoming_events"]}
    assert "Public project review" in event_titles
    assert "Private project prep" not in event_titles


def test_project_report_html_renders(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_report_project(sess)

    r = auth_client.get("/reports/project?project_number=7711.20")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Project Status" in html
    assert "Report project" in html
    assert "Late exhibit" in html
    assert "Public project review" in html


def test_project_report_errors_and_auth(client, auth_client):
    assert auth_client.get("/api/v1/reports/project").status_code == 400
    assert auth_client.get("/api/v1/reports/project?project_id=nope").status_code == 400
    assert auth_client.get("/api/v1/reports/project?project_number=missing").status_code == 404
    page = auth_client.get("/reports/project")
    assert page.status_code == 200
    assert "project_number or project_id is required" in page.get_data(as_text=True)

    with client.session_transaction() as s:
        s.clear()
    assert client.get("/api/v1/reports/project?project_number=7711.20").status_code == 401


def test_dashboard_workspace_exposes_project_report_action(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "workspaceOpenReport" in html
    assert "Project Report" in html
