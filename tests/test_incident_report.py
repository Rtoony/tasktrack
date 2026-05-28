"""Admin-only incident report tests."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import PersonnelIssue, Project


def _seed_incidents(sess):
    project = Project(project_number="9911.10", name="Incident project", client="Acme Water")
    sess.add(project)
    sess.flush()

    yesterday = datetime.now() - timedelta(days=1)
    due = (datetime.now() - timedelta(days=1)).date().isoformat()
    future = (datetime.now() + timedelta(days=7)).date().isoformat()

    open_issue = PersonnelIssue(
        person_name="Incident Employee",
        observed_by="Supervisor",
        cad_skill_area="Civil 3D",
        issue_description="Sensitive grading issue",
        incident_context="Private incident context",
        recommended_training="Private training plan",
        severity="High",
        status="Observed",
        reported_date=yesterday,
        follow_up_date=due,
        resolution_notes="",
        project_number="9911.10",
        project_id=project.id,
        estimated_time_loss_minutes=45,
        immediate_solution="Peer review before resubmittal",
    )
    resolved_issue = PersonnelIssue(
        person_name="Resolved Employee",
        observed_by="Supervisor",
        cad_skill_area="Plotting",
        issue_description="Resolved plotting issue",
        incident_context="Already handled",
        recommended_training="None",
        severity="Low",
        status="Resolved",
        reported_date=yesterday,
        follow_up_date=future,
        project_number="9911.10",
        project_id=project.id,
        estimated_time_loss_minutes=15,
    )
    sess.add_all([open_issue, resolved_issue])
    sess.commit()
    return {"project_id": project.id, "open_id": open_issue.id, "resolved_id": resolved_issue.id}


def test_incident_report_admin_json_html_csv(admin_client, temp_app):
    with temp_app.app_context():
        _seed_incidents(get_session())

    r = admin_client.get("/api/v1/reports/incidents?open_only=1")
    assert r.status_code == 200
    body = r.get_json()
    assert body["summary"]["total"] == 1
    assert body["summary"]["open_count"] == 1
    assert body["summary"]["high_severity_count"] == 1
    assert body["summary"]["follow_up_due_count"] == 1
    assert body["summary"]["estimated_time_loss_minutes"] == 45
    assert body["incidents"][0]["issue_description"] == "Sensitive grading issue"
    assert body["incidents"][0]["incident_context"] == "Private incident context"
    assert body["incidents"][0]["project_report_url"] == "/reports/project?project_number=9911.10"
    assert all(row["issue_description"] != "Resolved plotting issue" for row in body["incidents"])

    html = admin_client.get("/reports/incidents?open_only=1")
    assert html.status_code == 200
    page = html.get_data(as_text=True)
    assert "Incident Reports" in page
    assert "Sensitive grading issue" in page
    assert "Private incident context" in page
    assert "Peer review before resubmittal" in page
    assert "@page { size: letter" in page
    assert "/api/v1/reports/incidents.csv?open_only=1" in page
    assert "/reports/incidents/" in page
    assert "Resolved plotting issue" not in page

    csv_resp = admin_client.get("/api/v1/reports/incidents.csv?open_only=1")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["Content-Type"]
    text = csv_resp.get_data(as_text=True)
    assert "issue_description,incident_context" in text
    assert "Sensitive grading issue" in text
    assert "Private incident context" in text
    assert "Resolved plotting issue" not in text


def test_incident_report_filters(admin_client, temp_app):
    with temp_app.app_context():
        _seed_incidents(get_session())

    high = admin_client.get("/api/v1/reports/incidents?severity=High")
    assert high.status_code == 200
    assert [row["issue_description"] for row in high.get_json()["incidents"]] == ["Sensitive grading issue"]

    low = admin_client.get("/api/v1/reports/incidents?severity=Low")
    assert low.status_code == 200
    assert [row["issue_description"] for row in low.get_json()["incidents"]] == ["Resolved plotting issue"]

    search = admin_client.get("/api/v1/reports/incidents?q=grading&follow_up_due=1")
    assert search.status_code == 200
    assert search.get_json()["summary"]["total"] == 1

    project = admin_client.get("/api/v1/reports/incidents?project_number=9911.10&person=Incident")
    assert project.status_code == 200
    assert [row["person_name"] for row in project.get_json()["incidents"]] == ["Incident Employee"]


def test_incident_report_admin_only(auth_client):
    assert auth_client.get("/api/v1/reports/incidents").status_code == 403
    assert auth_client.get("/api/v1/reports/incidents.csv").status_code == 403
    assert auth_client.get("/api/v1/reports/incidents/1").status_code == 403
    assert auth_client.get("/reports/incidents", follow_redirects=False).status_code == 302
    assert auth_client.get("/reports/incidents/1", follow_redirects=False).status_code == 302

    with auth_client.session_transaction() as s:
        s.clear()
    assert auth_client.get("/api/v1/reports/incidents").status_code == 401
    assert auth_client.get("/api/v1/reports/incidents.csv").status_code == 401
    assert auth_client.get("/api/v1/reports/incidents/1").status_code == 401
    assert auth_client.get("/reports/incidents", follow_redirects=False).status_code == 302
    assert auth_client.get("/reports/incidents/1", follow_redirects=False).status_code == 302


def test_incident_report_links_from_report_center_and_admin(auth_client):
    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Admin User"
        s["user_role"] = "admin"

    admin_reports = auth_client.get("/reports")
    assert admin_reports.status_code == 200
    html = admin_reports.get_data(as_text=True)
    assert "Incident Reports" in html
    assert "Open Incidents" in html
    assert "High Severity" in html
    assert "Follow-Up Due" in html
    assert "/api/v1/reports/incidents.csv?open_only=1" in html

    with auth_client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"

    regular_reports = auth_client.get("/reports")
    assert regular_reports.status_code == 200
    assert "Incident Reports" not in regular_reports.get_data(as_text=True)

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Admin User"
        s["user_role"] = "admin"

    admin_page = auth_client.get("/admin")
    assert admin_page.status_code == 200
    admin_html = admin_page.get_data(as_text=True)
    assert "Incident Reports" in admin_html
    assert "High Severity Incidents" in admin_html
    assert "Incident CSV" in admin_html
    assert "/reports/incidents?open_only=1" in admin_html


def test_single_incident_report_admin_json_html(admin_client, temp_app):
    with temp_app.app_context():
        ids = _seed_incidents(get_session())

    r = admin_client.get(f"/api/v1/reports/incidents/{ids['open_id']}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["incident"]["id"] == ids["open_id"]
    assert body["incident"]["issue_description"] == "Sensitive grading issue"
    assert body["incident"]["incident_context"] == "Private incident context"
    assert body["project_report_url"] == "/reports/project?project_number=9911.10"

    html = admin_client.get(f"/reports/incidents/{ids['open_id']}")
    assert html.status_code == 200
    page = html.get_data(as_text=True)
    assert "Incident One-Pager" in page
    assert "Sensitive grading issue" in page
    assert "Private incident context" in page
    assert "Peer review before resubmittal" in page
    assert "/api/v1/reports/incidents/" in page
    assert "@page { size: letter" in page

    assert admin_client.get("/api/v1/reports/incidents/999999").status_code == 404
    assert admin_client.get("/reports/incidents/999999").status_code == 404


def test_today_brief_admin_incident_summary(auth_client, temp_app):
    with temp_app.app_context():
        _seed_incidents(get_session())

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Admin User"
        s["user_role"] = "admin"

    r = auth_client.get("/api/v1/reports/today")
    assert r.status_code == 200
    body = r.get_json()
    assert body["incidents"]["summary"]["open_count"] == 1
    assert body["incidents"]["summary"]["high_severity_count"] == 1
    assert body["incidents"]["incidents"][0]["issue_description"] == "Sensitive grading issue"

    html = auth_client.get("/reports/today")
    assert html.status_code == 200
    page = html.get_data(as_text=True)
    assert "Open Incident Follow-Ups" in page
    assert "Sensitive grading issue" in page
    assert "/reports/incidents/" in page

    with auth_client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"

    r = auth_client.get("/api/v1/reports/today")
    assert r.status_code == 200
    assert r.get_json()["incidents"] is None

    html = auth_client.get("/reports/today")
    assert html.status_code == 200
    assert "Open Incident Follow-Ups" not in html.get_data(as_text=True)
