"""Meeting packet report tests."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import CalendarEvent, PersonnelIssue, Project, ProjectWorkTask


def _future(days=2):
    return (datetime.now() + timedelta(days=days)).isoformat(timespec="minutes")


def _seed_meeting_packet(sess):
    proj = Project(
        project_number="7711.20",
        name="Meeting project",
        client="Acme Water",
        component="Site Improvement Plans",
        principal="Long, David",
        external_system="nexus-projects",
        external_ref="np-7711-20",
    )
    sess.add(proj)
    sess.flush()
    sess.add(ProjectWorkTask(
        project_name="Meeting project",
        title="Prepare management exhibit",
        task_description="Compile exhibits",
        project_number="7711.20",
        project_id=proj.id,
        status="In Progress",
    ))
    sess.add(PersonnelIssue(
        person_name="Sensitive Employee",
        issue_description="Sensitive capability narrative",
        incident_context="Private coaching context",
        recommended_training="Private remedial plan",
        resolution_notes="Private resolution notes",
        severity="High",
        status="Observed",
        project_number="7711.20",
        project_id=proj.id,
    ))
    meeting = CalendarEvent(
        title="Management sync",
        description="Review budget, constraints, and next actions.",
        event_type="meeting",
        status="scheduled",
        start_at=_future(2),
        end_at=_future(2),
        location="Conference Room A",
        project_number="7711.20",
        project_id=proj.id,
        visibility="internal",
        created_by_user_id=1,
    )
    private_project_event = CalendarEvent(
        title="Private project prep",
        event_type="prep",
        status="scheduled",
        start_at=_future(3),
        project_number="7711.20",
        project_id=proj.id,
        visibility="private",
        created_by_user_id=1,
    )
    unlinked = CalendarEvent(
        title="Unlinked staff sync",
        event_type="meeting",
        start_at=_future(4),
        visibility="internal",
        created_by_user_id=1,
    )
    private_event = CalendarEvent(
        title="Owner private meeting",
        event_type="meeting",
        start_at=_future(5),
        visibility="private",
        created_by_user_id=1,
    )
    sess.add_all([meeting, private_project_event, unlinked, private_event])
    sess.commit()
    return {
        "project_id": proj.id,
        "meeting_id": meeting.id,
        "private_project_event_id": private_project_event.id,
        "unlinked_id": unlinked.id,
        "private_event_id": private_event.id,
    }


def _ids(temp_app):
    with temp_app.app_context():
        return _seed_meeting_packet(get_session())


def test_meeting_packet_json_linked_event(auth_client, temp_app):
    ids = _ids(temp_app)

    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['meeting_id']}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["event"]["title"] == "Management sync"
    assert body["is_linked"] is True
    assert body["project"]["project_number"] == "7711.20"
    assert body["project_report"]["counts"]["project_work_tasks"] == 1
    assert all(event["id"] != ids["meeting_id"] for event in body["project_report"]["upcoming_events"])


def test_meeting_packet_json_unlinked_event(auth_client, temp_app):
    ids = _ids(temp_app)

    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['unlinked_id']}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["event"]["title"] == "Unlinked staff sync"
    assert body["is_linked"] is False
    assert body["project_report"] is None
    assert body["project"] is None


def test_meeting_packet_unknown_and_missing_event(auth_client, temp_app):
    _ids(temp_app)

    assert auth_client.get("/api/v1/reports/meeting").status_code == 400
    assert auth_client.get("/api/v1/reports/meeting?event_id=nope").status_code == 400
    assert auth_client.get("/api/v1/reports/meeting?event_id=999999").status_code == 404


def test_meeting_packet_private_event_hidden_from_other_user(auth_client, temp_app):
    ids = _ids(temp_app)

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"
    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['private_event_id']}")
    assert r.status_code == 404

    with auth_client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"
    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['private_event_id']}")
    assert r.status_code == 200


def test_meeting_packet_excludes_private_project_events_by_default(auth_client, temp_app):
    ids = _ids(temp_app)

    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['meeting_id']}")
    assert r.status_code == 200
    titles = {event["title"] for event in r.get_json()["project_report"]["upcoming_events"]}
    assert "Private project prep" not in titles

    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['meeting_id']}&include_private=1")
    assert r.status_code == 200
    titles = {event["title"] for event in r.get_json()["project_report"]["upcoming_events"]}
    assert "Private project prep" in titles


def test_meeting_packet_capabilities_gated_for_non_admin_and_admin(auth_client, temp_app):
    ids = _ids(temp_app)

    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['meeting_id']}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["capabilities_visible"] is False
    rows = body["project_report"]["linked_records"]["personnel_issues"]
    assert rows[0]["title"] == "Capability note (restricted)"
    assert rows[0]["redacted"] is True
    assert "Sensitive capability narrative" not in str(body)
    assert "Sensitive Employee" not in str(body)

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Admin User"
        s["user_role"] = "admin"
    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['meeting_id']}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["capabilities_visible"] is True
    rows = body["project_report"]["linked_records"]["personnel_issues"]
    assert rows[0]["issue_description"] == "Sensitive capability narrative"
    assert rows[0]["person_name"] == "Sensitive Employee"


def test_meeting_packet_html_renders_and_prints_without_capability_leak(auth_client, temp_app):
    ids = _ids(temp_app)

    r = auth_client.get(f"/reports/meeting?event_id={ids['meeting_id']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Meeting Packet" in html
    assert "Management sync" in html
    assert "7711.20" in html
    assert "@media print" in html
    assert "Capability note (restricted)" in html
    assert "Sensitive capability narrative" not in html


def test_calendar_ui_exposes_meeting_packet_actions(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "openMeetingPacketForEvent" in html
    assert "meeting-packet-btn" in html
    assert "Meeting Packet" in html
