"""Meeting packet report tests."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import ActivityLog, CalendarEvent, PersonnelIssue, Project, ProjectOverlay, ProjectWorkTask


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
    sess.add(ProjectOverlay(
        project_id=proj.id,
        project_number="7711.20",
        operator_status="Meeting prep watch",
        priority="High",
        internal_notes="Meeting internal secret",
        report_note="Meeting report note",
    ))
    task = ProjectWorkTask(
        project_name="Meeting project",
        title="Prepare management exhibit",
        task_description="Compile exhibits",
        project_number="7711.20",
        project_id=proj.id,
        status="In Progress",
    )
    sess.add(task)
    sess.flush()
    sess.add(ActivityLog(
        table_name="project_work_tasks",
        record_id=task.id,
        action="updated",
        field_name="status",
        old_value="Not Started",
        new_value="In Progress",
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
    assert body["project_report"]["management_brief"]["attention_level"] in {"active", "scheduled"}
    assert body["project_report"]["action_queue"][0]["title"] == "Prepare for Management sync"
    assert body["project_report"]["operator_overlay"]["report_note"] == "Meeting report note"
    assert body["project_report"]["operator_overlay"]["internal_notes"] == ""
    assert "Meeting internal secret" not in str(body)
    assert all(event["id"] != ids["meeting_id"] for event in body["project_report"]["upcoming_events"])


def test_meeting_packet_recent_project_activity(auth_client, temp_app):
    ids = _ids(temp_app)

    r = auth_client.get(f"/api/v1/reports/meeting?event_id={ids['meeting_id']}")
    assert r.status_code == 200
    activity = r.get_json()["project_report"]["recent_activity"]
    assert any(row["record_title"] == "Prepare management exhibit" for row in activity)
    assert any(row["new_value"] == "In Progress" for row in activity)


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
    assert "TaskTrack Meeting Packet" in html
    assert "print-masthead" in html
    assert "print-footer" in html
    assert "@page { size: letter" in html
    assert "Project Management Brief" in html
    assert "Meeting Action Queue" in html
    assert "Prepare for Management sync" in html
    assert "Meeting report note" in html
    assert "Meeting internal secret" not in html
    assert "Management sync" in html
    assert "7711.20" in html
    assert '/?workspace=7711.20' in html
    assert '/?map_project=7711.20' in html
    assert "@media print" in html
    assert "Capability note (restricted)" in html
    assert "Sensitive capability narrative" not in html


def test_meeting_packet_batch_json_and_html(auth_client, temp_app):
    ids = _ids(temp_app)

    r = auth_client.get("/api/v1/reports/meetings?days=14&limit=10")
    assert r.status_code == 200
    body = r.get_json()
    titles = [packet["event"]["title"] for packet in body["packets"]]
    assert "Management sync" in titles
    assert "Unlinked staff sync" in titles
    assert "Owner private meeting" not in titles
    assert body["include_private"] is False
    assert body["count"] == 2
    assert "Private project prep" not in str(body)

    r = auth_client.get("/api/v1/reports/meetings?days=14&limit=10&include_private=1")
    assert r.status_code == 200
    titles = [packet["event"]["title"] for packet in r.get_json()["packets"]]
    assert "Owner private meeting" in titles

    r = auth_client.get("/api/v1/reports/meetings?days=14&limit=10&project_number=7711.20")
    assert r.status_code == 200
    project_titles = [packet["event"]["title"] for packet in r.get_json()["packets"]]
    assert project_titles == ["Management sync"]

    r = auth_client.get("/reports/meetings?days=14&limit=10")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Meeting Packet Batch" in html
    assert "TaskTrack Meeting Packet Batch" in html
    assert "Management sync" in html
    assert "Unlinked staff sync" in html
    assert "Owner private meeting" not in html
    assert "window.print" in html
    assert "@page { size: letter" in html
    assert "Saved Preset" in html
    assert "saveMeetingPreset" in html
    assert "updateMeetingPreset" in html
    assert "deleteMeetingPreset" in html


def test_meeting_packet_batch_presets(auth_client, temp_app):
    _ids(temp_app)

    create = auth_client.post("/api/v1/reports/presets", json={
        "name": "Two week meeting batch",
        "surface": "meetings",
        "is_shared": True,
        "filters": {
            "days": 14,
            "limit": 10,
            "project_number": "7711.20",
            "event_type": "meeting",
            "include_private": True,
        },
    })
    assert create.status_code == 201
    preset = create.get_json()
    assert preset["surface"] == "meetings"
    assert preset["filters"]["project_number"] == "7711.20"
    assert preset["filters"]["include_private"] is True

    listed = auth_client.get("/api/v1/reports/presets?surface=meetings")
    assert listed.status_code == 200
    assert [row["name"] for row in listed.get_json()["presets"]] == ["Two week meeting batch"]

    packet = auth_client.get(f"/api/v1/reports/meetings?preset={preset['id']}")
    assert packet.status_code == 200
    body = packet.get_json()
    assert body["selected_preset"]["id"] == preset["id"]
    assert body["filters"]["project_number"] == "7711.20"
    assert body["filters"]["event_type"] == "meeting"
    assert body["include_private"] is True
    assert [row["event"]["title"] for row in body["packets"]] == ["Management sync"]

    html = auth_client.get(f"/reports/meetings?preset={preset['id']}")
    assert html.status_code == 200
    page = html.get_data(as_text=True)
    assert "Loaded preset: Two week meeting batch" in page
    assert "Management sync" in page
    assert "Unlinked staff sync" not in page

    override = auth_client.get(f"/api/v1/reports/meetings?preset={preset['id']}&project_number=")
    assert override.status_code == 200


def test_meeting_presets_visible_on_report_center(auth_client):
    preset = auth_client.post("/api/v1/reports/presets", json={
        "name": "Shared meeting packet",
        "surface": "meetings",
        "is_shared": True,
        "filters": {"days": 7, "limit": 5},
    }).get_json()

    r = auth_client.get("/reports")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Saved Meeting Presets" in html
    assert "Shared meeting packet" in html
    assert f'/reports/meetings?preset={preset["id"]}' in html


def test_calendar_ui_exposes_meeting_packet_actions(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "openMeetingPacketForEvent" in html
    assert "meeting-packet-btn" in html
    assert "Meeting Packet" in html
