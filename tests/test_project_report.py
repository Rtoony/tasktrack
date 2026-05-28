"""Project status report MVP tests."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import ActivityLog, CalendarEvent, PersonnelIssue, Project, ProjectOverlay, ProjectSite, ProjectWorkTask, ReportPreset, WorkTask


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
    sess.add(ProjectOverlay(
        project_id=proj.id,
        project_number="7711.20",
        operator_status="Needs PM review",
        priority="High",
        tags="budget, meeting",
        next_review_date="2026-06-01",
        internal_notes="Internal-only project context",
        report_note="Management-facing overlay note",
    ))
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
    assert body["management_brief"]["attention_level"] == "at_risk"
    assert body["management_brief"]["overdue_count"] == 1
    assert body["management_brief"]["top_overdue"][0]["title"] == "Late exhibit"
    assert body["action_queue"][0]["priority"] == "high"
    assert body["action_queue"][0]["title"] == "Resolve overdue Project Tasks: Late exhibit"
    assert any(action["title"] == "Management note" for action in body["action_queue"])
    assert body["operator_overlay"]["operator_status"] == "Needs PM review"
    assert body["operator_overlay"]["priority"] == "High"
    assert body["operator_overlay"]["report_note"] == "Management-facing overlay note"
    assert body["operator_overlay"]["internal_notes"] == ""
    assert "Internal-only project context" not in str(body)
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
    assert "TaskTrack Project Status" in html
    assert "print-masthead" in html
    assert "print-footer" in html
    assert "@page { size: letter" in html
    assert "Management Brief" in html
    assert "Management Action Queue" in html
    assert "Resolve overdue Project Tasks: Late exhibit" in html
    assert "TaskTrack Overlay" in html
    assert "Management-facing overlay note" in html
    assert "Internal-only project context" not in html
    assert "project-report-picker-options" in html
    assert "normalizeProjectReportLookup" in html
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
    sess.add(ProjectOverlay(
        project_id=p1.id,
        project_number="8800.10",
        operator_status="Portfolio watch",
        priority="Medium",
        tags="portfolio",
    ))
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
    assert body["summary"]["attention_project_count"] == 1
    assert body["summary"]["action_projects"][0]["project_number"] == "8800.10"
    assert body["summary"]["action_projects"][0]["primary_action"] == "Resolve overdue Project Tasks: Portfolio late item"
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

    r = auth_client.get("/api/v1/reports/projects?client=Acme&attention_level=at_risk&limit=5")
    assert r.status_code == 200
    body = r.get_json()
    assert body["filters"]["attention_level"] == "at_risk"
    assert [report["project"]["project_number"] for report in body["reports"]] == ["8800.10"]

    r = auth_client.get("/api/v1/reports/projects?client=Acme&attention_level=quiet&limit=5")
    assert r.status_code == 200
    quiet_numbers = [report["project"]["project_number"] for report in r.get_json()["reports"]]
    assert "8800.20" in quiet_numbers
    assert "8800.10" not in quiet_numbers


def test_portfolio_project_report_html_renders(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_portfolio_projects(sess)

    r = auth_client.get("/reports/projects?client=Acme&limit=5")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Portfolio Project Packet" in html
    assert "TaskTrack Portfolio Packet" in html
    assert "print-masthead" in html
    assert "print-footer" in html
    assert "@page { size: letter" in html
    assert "At Risk Projects" in html
    assert 'id="attention_level"' in html
    assert "Management Action Queue" in html
    assert "What to discuss first" in html
    assert "Actions CSV" in html
    assert "Resolve overdue Project Tasks: Portfolio late item" in html
    assert "Brief:" in html
    assert "Overlay:" in html
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
    assert 'href="/reports"' in html

    with client.session_transaction() as s:
        s.clear()
    assert client.get("/api/v1/reports/projects").status_code == 401
    assert client.get("/reports/projects").status_code == 302
    assert client.get("/reports").status_code == 302

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


def test_report_preset_crud_and_apply_portfolio_filters(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_portfolio_projects(sess)

    create = auth_client.post("/api/v1/reports/presets", json={
        "name": "Acme active packet",
        "surface": "portfolio",
        "filters": {
            "client": "Acme",
            "component": "Site Improvement Plans",
            "project_numbers": ["8800.10"],
            "include_private": True,
            "limit": 5,
        },
    })
    assert create.status_code == 201
    preset = create.get_json()
    assert preset["name"] == "Acme active packet"
    assert preset["filters"]["client"] == "Acme"
    assert preset["filters"]["include_private"] is True

    listed = auth_client.get("/api/v1/reports/presets?surface=portfolio")
    assert listed.status_code == 200
    assert [row["name"] for row in listed.get_json()["presets"]] == ["Acme active packet"]

    packet = auth_client.get(f"/api/v1/reports/projects?preset={preset['id']}")
    assert packet.status_code == 200
    body = packet.get_json()
    assert body["selected_preset"]["id"] == preset["id"]
    assert body["filters"]["client"] == "Acme"
    assert body["include_private"] is True
    assert body["summary"]["project_count"] == 1
    titles = {event["title"] for event in body["reports"][0]["upcoming_events"]}
    assert "Private portfolio prep" in titles

    override = auth_client.get(f"/api/v1/reports/projects?preset={preset['id']}&include_private=0")
    assert override.status_code == 200
    titles = {event["title"] for event in override.get_json()["reports"][0]["upcoming_events"]}
    assert "Private portfolio prep" not in titles


def test_report_preset_update_overwrites_loaded_filters(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_portfolio_projects(sess)

    preset = auth_client.post("/api/v1/reports/presets", json={
        "name": "Original packet",
        "surface": "portfolio",
        "filters": {"client": "Acme", "limit": 5},
    }).get_json()

    update = auth_client.put(f"/api/v1/reports/presets/{preset['id']}", json={
        "name": "Dormant packet",
        "surface": "portfolio",
        "is_shared": True,
        "filters": {
            "client": "Acme",
            "project_numbers": ["8800.20"],
            "include_inactive": True,
            "limit": 5,
        },
    })
    assert update.status_code == 200
    body = update.get_json()
    assert body["name"] == "Dormant packet"
    assert body["is_shared"] is True
    assert body["filters"]["project_numbers"] == ["8800.20"]

    packet = auth_client.get(f"/api/v1/reports/projects?preset={preset['id']}")
    assert packet.status_code == 200
    report_body = packet.get_json()
    assert report_body["selected_preset"]["name"] == "Dormant packet"
    assert report_body["filters"]["include_inactive"] is True
    assert report_body["summary"]["project_count"] == 1
    assert report_body["reports"][0]["project"]["project_number"] == "8800.20"


def test_report_preset_update_forbidden_for_shared_non_owner(auth_client):
    shared = auth_client.post("/api/v1/reports/presets", json={
        "name": "Shared owner packet",
        "surface": "portfolio",
        "is_shared": True,
        "filters": {"client": "Acme"},
    }).get_json()

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    update = auth_client.put(f"/api/v1/reports/presets/{shared['id']}", json={
        "name": "Hijacked",
        "surface": "portfolio",
        "filters": {"client": "Beta"},
    })
    assert update.status_code == 403

    visible = auth_client.get(f"/api/v1/reports/projects?preset={shared['id']}")
    assert visible.status_code == 200
    assert visible.get_json()["selected_preset"]["name"] == "Shared owner packet"


def test_report_preset_owner_visibility_and_delete(auth_client, temp_app):
    private = auth_client.post("/api/v1/reports/presets", json={
        "name": "Owner only",
        "surface": "portfolio",
        "filters": {"client": "Acme"},
    }).get_json()
    shared = auth_client.post("/api/v1/reports/presets", json={
        "name": "Shared packet",
        "surface": "portfolio",
        "is_shared": True,
        "filters": {"client": "Beta"},
    }).get_json()

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    listed = auth_client.get("/api/v1/reports/presets?surface=portfolio")
    assert listed.status_code == 200
    names = {row["name"] for row in listed.get_json()["presets"]}
    assert "Shared packet" in names
    assert "Owner only" not in names
    assert auth_client.get(f"/api/v1/reports/projects?preset={private['id']}").status_code == 404
    assert auth_client.delete(f"/api/v1/reports/presets/{shared['id']}").status_code == 403

    with auth_client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"
    assert auth_client.delete(f"/api/v1/reports/presets/{shared['id']}").status_code == 200
    assert auth_client.get(f"/api/v1/reports/projects?preset={shared['id']}").status_code == 404


def test_report_preset_validation_and_auth(client, auth_client):
    bad_key = auth_client.post("/api/v1/reports/presets", json={
        "name": "Bad",
        "surface": "portfolio",
        "filters": {"sql": "nope"},
    })
    assert bad_key.status_code == 400
    assert "unsupported filter key" in bad_key.get_json()["error"]

    bad_surface = auth_client.post("/api/v1/reports/presets", json={
        "name": "Bad",
        "surface": "meeting",
        "filters": {},
    })
    assert bad_surface.status_code == 400
    assert auth_client.get("/api/v1/reports/projects?preset=nope").status_code == 400

    with client.session_transaction() as s:
        s.clear()
    assert client.get("/api/v1/reports/presets").status_code == 401
    assert client.post("/api/v1/reports/presets", json={"name": "x"}).status_code == 401


def test_portfolio_report_html_exposes_preset_controls(auth_client):
    r = auth_client.get("/reports/projects")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Saved Preset" in html
    assert "Find Project" in html
    assert "project-picker-options" in html
    assert "addPickedProject" in html
    assert "savePortfolioPreset" in html
    assert "updatePortfolioPreset" in html
    assert "deletePortfolioPreset" in html
    assert 'id="attention_level"' in html

def test_today_brief_json_and_html(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_portfolio_projects(sess)
        today = (datetime.now() + timedelta(minutes=15)).isoformat(timespec="minutes")
        proj = sess.query(Project).filter_by(project_number="8800.10").one()
        sess.add(CalendarEvent(
            title="Today project sync",
            event_type="meeting",
            status="scheduled",
            start_at=today,
            project_number="8800.10",
            project_id=proj.id,
            visibility="internal",
            created_by_user_id=1,
        ))
        sess.add(CalendarEvent(
            title="Private today prep",
            event_type="prep",
            status="scheduled",
            start_at=today,
            project_number="8800.10",
            project_id=proj.id,
            visibility="private",
            created_by_user_id=1,
        ))
        sess.commit()

    r = auth_client.get("/api/v1/reports/today")
    assert r.status_code == 200
    body = r.get_json()
    assert body["meetings"]["count"] >= 1
    assert any(packet["event"]["title"] == "Today project sync" for packet in body["meetings"]["packets"])
    assert any(row["project_number"] == "8800.10" for row in body["action_projects"])
    assert "Private today prep" not in str(body)

    r = auth_client.get("/reports/today")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Today Brief" in html
    assert "TaskTrack Today Brief" in html
    assert "Today project sync" in html
    assert "At-Risk Action Queue" in html
    assert "Private today prep" not in html
    assert "@page { size: letter" in html


def test_portfolio_action_queue_csv_export(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_portfolio_projects(sess)

    r = auth_client.get("/api/v1/reports/projects/actions.csv?client=Acme&attention_level=at_risk&limit=5")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("Content-Type", "")
    body = r.get_data(as_text=True)
    assert "project_number,name,client,attention_level,primary_action" in body
    assert "8800.10,Portfolio one,Acme Water,at_risk" in body
    assert "Resolve overdue Project Tasks: Portfolio late item" in body
    assert "/reports/project?project_number=8800.10" in body
    assert "Private portfolio prep" not in body


def test_reports_home_renders_command_center(client, auth_client):
    preset = auth_client.post("/api/v1/reports/presets", json={
        "name": "Management review",
        "surface": "portfolio",
        "filters": {"client": "Acme", "limit": 5},
    }).get_json()

    r = auth_client.get("/reports")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Report Center" in html
    assert "Testing Launchpad" in html
    assert "Triage Inbox" in html
    assert "/?tab=triage" in html
    assert "/?tab=calendar" in html
    assert "Project Status One-Pager" in html
    assert "Today Brief" in html
    assert "At-Risk Queue" in html
    assert "attention_level=at_risk" in html
    assert "At-Risk CSV" in html
    assert "Upcoming Meeting Packets" in html
    assert "Batch Meeting Packets" in html
    assert "/reports/meetings?days=14&limit=12" in html
    assert "loadUpcomingMeetingPackets" in html
    assert "openProjectReport" in html
    assert f'/reports/projects?preset={preset["id"]}' in html
    assert "Management review" in html

    with client.session_transaction() as s:
        s.clear()
    assert client.get("/reports").status_code == 302
