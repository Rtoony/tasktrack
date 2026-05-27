"""Project status report MVP tests."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import ActivityLog, CalendarEvent, PersonnelIssue, Project, ProjectSite, ProjectWorkTask, WorkTask


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
    sess.add(PersonnelIssue(
        person_name="Sensitive Employee",
        issue_description="Sensitive report narrative",
        incident_context="Private incident context",
        recommended_training="Private training plan",
        severity="High",
        status="Observed",
        project_number="7711.20",
        project_id=proj.id,
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
    assert '/?workspace=7711.20' in html
    assert '/?map_project=7711.20' in html
    assert "Private project prep" not in html


def test_project_report_redacts_capability_narratives_for_non_admin(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_report_project(sess)

    r = auth_client.get("/api/v1/reports/project?project_number=7711.20")
    assert r.status_code == 200
    body = r.get_json()
    rows = body["linked_records"]["personnel_issues"]
    assert rows[0]["title"] == "Capability note (restricted)"
    assert rows[0]["redacted"] is True
    assert "Sensitive report narrative" not in str(body)
    assert "Sensitive Employee" not in str(body)

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Admin User"
        s["user_role"] = "admin"
    r = auth_client.get("/api/v1/reports/project?project_number=7711.20")
    assert r.status_code == 200
    body = r.get_json()
    rows = body["linked_records"]["personnel_issues"]
    assert rows[0]["issue_description"] == "Sensitive report narrative"
    assert rows[0]["person_name"] == "Sensitive Employee"



def test_project_report_recent_activity_privacy(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj = Project(project_number="7722.10", name="Activity project")
        sess.add(proj)
        sess.flush()
        task = WorkTask(
            title="Visible activity task",
            project_number="7722.10",
            project_id=proj.id,
            status="In Progress",
        )
        issue = PersonnelIssue(
            person_name="Activity Employee",
            issue_description="Sensitive activity narrative",
            project_number="7722.10",
            project_id=proj.id,
        )
        sess.add_all([task, issue])
        sess.flush()
        sess.add_all([
            ActivityLog(
                table_name="work_tasks",
                record_id=task.id,
                action="updated",
                field_name="status",
                old_value="Not Started",
                new_value="In Progress",
            ),
            ActivityLog(
                table_name="personnel_issues",
                record_id=issue.id,
                action="updated",
                field_name="issue_description",
                old_value="old sensitive activity narrative",
                new_value="Sensitive activity narrative",
            ),
        ])
        sess.commit()

    r = auth_client.get("/api/v1/reports/project?project_number=7722.10")
    assert r.status_code == 200
    body = r.get_json()
    assert any(row["record_title"] == "Visible activity task" for row in body["recent_activity"])
    assert "Sensitive activity narrative" not in str(body)
    assert not any(row["table_name"] == "personnel_issues" for row in body["recent_activity"])

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Admin User"
        s["user_role"] = "admin"
    r = auth_client.get("/api/v1/reports/project?project_number=7722.10")
    assert r.status_code == 200
    body = r.get_json()
    assert any(row["table_name"] == "personnel_issues" for row in body["recent_activity"])
    assert "Sensitive activity narrative" in str(body)


def test_project_report_can_include_own_private_when_requested(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_report_project(sess)

    r = auth_client.get("/api/v1/reports/project?project_number=7711.20")
    assert r.status_code == 200
    default_titles = {event["title"] for event in r.get_json()["upcoming_events"]}
    assert "Private project prep" not in default_titles

    r = auth_client.get("/api/v1/reports/project?project_number=7711.20&include_private=1")
    assert r.status_code == 200
    titles = {event["title"] for event in r.get_json()["upcoming_events"]}
    assert "Public project review" in titles
    assert "Private project prep" in titles


def _seed_portfolio_projects(sess):
    p1 = Project(
        project_number="8800.10",
        name="Portfolio one",
        client="Acme Water",
        component="Site Improvement Plans",
        principal="Long, David",
        display_status="active",
    )
    p2 = Project(
        project_number="8800.20",
        name="Portfolio dormant",
        client="Acme Water",
        component="Topographic Mapping",
        principal="Long, David",
        display_status="dormant",
    )
    p3 = Project(
        project_number="9900.30",
        name="Other client project",
        client="Other District",
        component="Site Improvement Plans",
        principal="Ng, Maya",
        display_status="active",
    )
    p4 = Project(
        project_number="8800.40",
        name="Soft deleted portfolio",
        client="Acme Water",
        component="Site Improvement Plans",
        principal="Long, David",
        display_status="active",
        active=0,
    )
    sess.add_all([p1, p2, p3, p4])
    sess.flush()
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    future = (datetime.now() + timedelta(days=3)).isoformat(timespec="minutes")
    sess.add(ProjectSite(project_id=p1.id, lat=38.5, lng=-122.5, pin_color="green", is_primary=1))
    sess.add(ProjectWorkTask(
        project_name="Portfolio one",
        title="Portfolio late item",
        project_number="8800.10",
        project_id=p1.id,
        task_description="Finish packet",
        status="In Progress",
        due_at=yesterday + "T17:00",
    ))
    sess.add(CalendarEvent(
        title="Public portfolio review",
        event_type="review",
        start_at=future,
        project_number="8800.10",
        project_id=p1.id,
        visibility="internal",
        created_by_user_id=1,
    ))
    sess.add(CalendarEvent(
        title="Private portfolio prep",
        event_type="prep",
        start_at=future,
        project_number="8800.10",
        project_id=p1.id,
        visibility="private",
        created_by_user_id=1,
    ))
    sess.commit()


def test_portfolio_project_report_filters_summary_and_privacy(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_portfolio_projects(sess)

    r = auth_client.get(
        "/api/v1/reports/projects?client=Acme&component=Site%20Improvement%20Plans"
        "&display_status=active&limit=5"
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["summary"]["project_count"] == 1
    assert body["summary"]["site_count"] == 1
    assert body["summary"]["overdue_count"] == 1
    assert body["include_private"] is False
    report = body["reports"][0]
    assert report["project"]["project_number"] == "8800.10"
    titles = {event["title"] for event in report["upcoming_events"]}
    assert "Public portfolio review" in titles
    assert "Private portfolio prep" not in titles

    r = auth_client.get(
        "/api/v1/reports/projects?project_numbers=8800.10&include_private=1&limit=5"
    )
    assert r.status_code == 200
    titles = {event["title"] for event in r.get_json()["reports"][0]["upcoming_events"]}
    assert "Private portfolio prep" in titles


def test_portfolio_project_report_html_renders(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_portfolio_projects(sess)

    r = auth_client.get("/reports/projects?client=Acme&limit=5")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Portfolio Project Packet" in html
    assert "Portfolio one" in html
    assert "Portfolio dormant" in html
    assert '/?workspace=8800.10' in html
    assert '/?map_project=8800.10' in html
    assert "Soft deleted portfolio" not in html
    assert "Private portfolio prep" not in html


def test_portfolio_project_report_auth_and_dashboard_link(client, auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/reports/projects"' in html

    with client.session_transaction() as s:
        s.clear()
    assert client.get("/api/v1/reports/projects").status_code == 401
    assert client.get("/reports/projects").status_code == 302

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
