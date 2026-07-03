"""Thursday Packet — service section windows, route auth, and HTML rendering.

Mirrors the conftest client/temp_app/auth_client fixture pattern used by
test_digest.py / test_calendar.py.
"""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import Project, ProjectWorkTask, WorkTask
from app.services.thursday_packet import thursday_packet


def _seed(temp_app):
    """Seed the four spec scenarios plus exclusion cases across both tables."""
    now = datetime.now().replace(microsecond=0)
    with temp_app.app_context():
        sess = get_session()
        sess.add_all([
            # completed inside the 7-day window (both tables)
            ProjectWorkTask(
                title="Done this week", project_number="1000.01",
                engineer="Ann Engineer", status="Complete",
                updated_at=now - timedelta(days=2),
            ),
            WorkTask(
                title="CAD done this week", requested_by="Cara Requestor",
                status="Complete", updated_at=now - timedelta(days=1),
            ),
            # completed OUTSIDE the window — must not appear
            ProjectWorkTask(
                title="Done long ago", project_number="1000.02",
                engineer="Ann Engineer", status="Complete",
                updated_at=now - timedelta(days=30),
            ),
            # archived completion — must not appear
            ProjectWorkTask(
                title="Archived done", status="Complete",
                updated_at=now - timedelta(days=1),
                archived_at=now,
            ),
            # in flight: due inside the next 7 days
            ProjectWorkTask(
                title="Due soon", project_number="1000.03", engineer="Bob Builder",
                status="Not Started",
                due_at=(now + timedelta(days=3)).isoformat(timespec="minutes"),
            ),
            # in flight: no due date but explicitly In Progress
            WorkTask(
                title="Rolling CAD work", requested_by="Cara Requestor",
                status="In Progress",
            ),
            # due far beyond the window and not in progress — must not appear
            ProjectWorkTask(
                title="Far future", project_number="1000.05",
                status="Not Started",
                due_at=(now + timedelta(days=30)).isoformat(timespec="minutes"),
            ),
            # overdue rows (both tables) — risks, never in_flight
            ProjectWorkTask(
                title="Overdue item", project_number="1000.04",
                engineer="Bob Builder", status="In Progress",
                due_at=(now - timedelta(days=2)).isoformat(timespec="minutes"),
            ),
            WorkTask(
                title="CAD overdue", requested_by="Cara Requestor",
                status="Not Started",
                due_date=(now - timedelta(days=3)).date().isoformat(),
            ),
        ])
        sess.commit()


def _titles(rows):
    return {r["title"] for r in rows}


def test_thursday_packet_section_membership(temp_app):
    _seed(temp_app)
    with temp_app.app_context():
        packet = thursday_packet(get_session())

    completed = _titles(packet["completed"])
    assert "Done this week" in completed
    assert "CAD done this week" in completed
    assert "Done long ago" not in completed        # outside window
    assert "Archived done" not in completed        # archived

    in_flight = _titles(packet["in_flight"])
    assert "Due soon" in in_flight
    assert "Rolling CAD work" in in_flight         # In Progress, no due date
    assert "Far future" not in in_flight           # due beyond window
    assert "Overdue item" not in in_flight         # overdue -> risks only

    overdue = _titles(packet["risks"]["overdue"])
    assert "Overdue item" in overdue
    assert "CAD overdue" in overdue

    assert packet["counts"]["completed"] == 2
    assert packet["counts"]["in_flight"] == 2
    assert packet["counts"]["overdue"] == 2
    assert packet["window"]["week_of"].startswith("Week of ")


def test_thursday_packet_row_shape_and_person_mapping(temp_app):
    _seed(temp_app)
    with temp_app.app_context():
        packet = thursday_packet(get_session())

    by_title = {r["title"]: r for r in packet["completed"]}
    pwt = by_title["Done this week"]
    assert pwt["table"] == "project_work_tasks"
    assert pwt["project_number"] == "1000.01"
    assert pwt["person"] == "Ann Engineer"          # engineer column
    assert pwt["date"]

    wt = by_title["CAD done this week"]
    assert wt["table"] == "work_tasks"
    assert wt["person"] == "Cara Requestor"         # requested_by column


def test_thursday_packet_excludes_calendar_personnel_personal(temp_app):
    """Management-facing packet: only the two work tables plus the portfolio
    at-risk summary — no calendar/personnel/personal payloads anywhere."""
    _seed(temp_app)
    with temp_app.app_context():
        packet = thursday_packet(get_session())
    tables = {r["table"] for r in
              packet["completed"] + packet["in_flight"] + packet["risks"]["overdue"]}
    assert tables <= {"project_work_tasks", "work_tasks"}
    assert set(packet) == {
        "generated_at", "window", "completed", "in_flight", "risks", "counts",
    }


def test_thursday_packet_at_risk_projects_from_portfolio(temp_app, auth_client):
    now = datetime.now().replace(microsecond=0)
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="9100.10", name="Risky Bridge",
                         display_status="active"))
        sess.add(ProjectWorkTask(
            title="Late deliverable", project_number="9100.10", project_id=1,
            engineer="Bob Builder", status="In Progress",
            due_at=(now - timedelta(days=4)).isoformat(timespec="minutes"),
        ))
        sess.commit()
        packet = thursday_packet(sess)

    numbers = {r.get("project_number") for r in packet["risks"]["at_risk_projects"]}
    assert "9100.10" in numbers
    assert packet["risks"]["at_risk_count"] >= 1


def test_thursday_json_requires_session(client):
    r = client.get("/api/v1/reports/thursday")
    assert r.status_code == 401


def test_thursday_page_redirects_anonymous(client):
    r = client.get("/reports/thursday")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_thursday_json_sections(auth_client, temp_app):
    _seed(temp_app)
    r = auth_client.get("/api/v1/reports/thursday")
    assert r.status_code == 200
    body = r.get_json()
    assert "Done this week" in _titles(body["completed"])
    assert "Due soon" in _titles(body["in_flight"])
    assert "Overdue item" in _titles(body["risks"]["overdue"])
    assert body["window"]["days"] == 7
    assert body["window"]["due_days"] == 7

    # window args are clamped + honored
    r = auth_client.get("/api/v1/reports/thursday?window_days=1&due_days=1")
    assert r.status_code == 200
    assert r.get_json()["window"]["days"] == 1


def test_thursday_page_renders_letterhead_and_sections(auth_client, temp_app):
    _seed(temp_app)
    r = auth_client.get("/reports/thursday")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Thursday Packet" in html
    assert "Completed This Week" in html
    assert "In Flight" in html
    assert "Risks &amp; Overdue" in html
    assert "Week of " in html
    # letterhead defaults (empty company -> department is the top line)
    assert "CAD Department" in html
    assert "Prepared by Josh Patheal — CAD Technical Lead" in html
    # shared shells: sidebar + the one shared print stylesheet + print button
    assert "report-shell.css" in html
    assert "report-print.css" in html
    assert 'href="/reports/thursday" class="active"' in html
    assert "window.print()" in html
    assert "Print / Save as PDF" in html
    # seeded rows render in the tables
    assert "Done this week" in html
    assert "Overdue item" in html


def test_thursday_page_empty_sections_render_none_this_week(auth_client):
    r = auth_client.get("/reports/thursday")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert html.count("None this week.") == 3


def test_thursday_letterhead_uses_configured_company(auth_client, temp_app):
    from app.services.report_settings import set_report_letterhead
    with temp_app.app_context():
        sess = get_session()
        set_report_letterhead(sess, {"company": "Acme Engineering Group"})
        sess.commit()
    html = auth_client.get("/reports/thursday").get_data(as_text=True)
    assert "Acme Engineering Group" in html
    assert "CAD Department" in html   # department line under the company
