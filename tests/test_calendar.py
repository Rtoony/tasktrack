"""Internal calendar MVP tests."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import Project


def _future(days=1, hour_offset=0):
    value = datetime.now().replace(microsecond=0) + timedelta(days=days, hours=hour_offset)
    return value.isoformat(timespec="minutes")


def _past(days=1):
    value = datetime.now().replace(microsecond=0) - timedelta(days=days)
    return value.isoformat(timespec="minutes")


def _create_event(auth_client, **overrides):
    payload = {
        "title": "Ops meeting",
        "event_type": "meeting",
        "start_at": _future(),
    }
    payload.update(overrides)
    r = auth_client.post("/api/v1/calendar_events", json=payload)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_calendar_events_crud_and_project_enrichment(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="1234.56", name="Bridge Study"))
        sess.commit()

    created = _create_event(
        auth_client,
        title="Bridge kickoff",
        event_type="milestone",
        project_number="1234.56",
        location="Conference Room",
        all_day=True,
    )
    assert created["status"] == "scheduled"
    assert created["all_day"] == 1
    assert created["project_id"] == 1
    assert created["created_by_user_id"] == 1

    event_id = created["id"]
    r = auth_client.get(f"/api/v1/calendar_events/{event_id}")
    assert r.status_code == 200
    assert r.get_json()["title"] == "Bridge kickoff"

    r = auth_client.put(
        f"/api/v1/calendar_events/{event_id}",
        json={"status": "done", "description": "Meeting completed"},
    )
    assert r.status_code == 200
    assert r.get_json()["status"] == "done"

    r = auth_client.delete(f"/api/v1/calendar_events/{event_id}")
    assert r.status_code == 200
    assert auth_client.get(f"/api/v1/calendar_events/{event_id}").status_code == 404


def test_calendar_create_validation(auth_client):
    cases = [
        ({"title": "No start"}, "'start_at' is required"),
        ({"title": "Bad type", "start_at": _future(), "event_type": "party"}, "event_type"),
        ({"title": "Bad status", "start_at": _future(), "status": "open"}, "status"),
        ({"title": "Bad visibility", "start_at": _future(), "visibility": "public"}, "visibility"),
        ({"title": "Bad date", "start_at": "tomorrow"}, "Start"),
        ({"title": "Bad end", "start_at": _future(2), "end_at": _future(1)}, "end_at"),
        ({"title": "Bad related", "start_at": _future(), "related_table": "nope"}, "related_table"),
    ]
    for payload, expected in cases:
        r = auth_client.post("/api/v1/calendar_events", json=payload)
        assert r.status_code == 400
        assert expected in r.get_json()["error"]


def test_upcoming_events_filters_window_and_status(auth_client):
    future_a = _create_event(auth_client, title="Soon", start_at=_future(1))
    _create_event(auth_client, title="Later", start_at=_future(20))
    _create_event(auth_client, title="Past", start_at=_past(1))
    _create_event(auth_client, title="Cancelled", start_at=_future(1, 1), status="cancelled")

    r = auth_client.get("/api/v1/calendar/upcoming?days=7&limit=10")
    assert r.status_code == 200
    body = r.get_json()
    assert body["available"] is True
    titles = [event["title"] for event in body["events"]]
    assert titles == ["Soon"]
    assert body["events"][0]["id"] == future_a["id"]


def test_range_events_filters_by_project_type_and_status(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="2222.00", name="Waterline"))
        sess.commit()

    meeting = _create_event(
        auth_client,
        title="Waterline review",
        event_type="review",
        status="tentative",
        project_number="2222.00",
        start_at=_future(3),
    )
    _create_event(auth_client, title="Other", event_type="meeting", start_at=_future(3))

    r = auth_client.get(
        f"/api/v1/calendar/events?from={datetime.now().date().isoformat()}"
        f"&to={(datetime.now() + timedelta(days=10)).date().isoformat()}"
        f"&project_id={meeting['project_id']}&type=review&status=tentative"
    )
    assert r.status_code == 200
    rows = r.get_json()
    assert [row["title"] for row in rows] == ["Waterline review"]
    assert rows[0]["event_type"] == "review"
    assert rows[0]["project_number"] == "2222.00"


def test_range_events_support_date_window_all_day_and_sorting(auth_client):
    target = (datetime.now() + timedelta(days=4)).date().isoformat()
    all_day = _create_event(
        auth_client,
        title="All day site visit",
        start_at=target,
        all_day=True,
    )
    timed = _create_event(
        auth_client,
        title="Afternoon review",
        start_at=target + "T14:30",
    )

    r = auth_client.get(f"/api/v1/calendar/events?from={target}&to={target}")
    assert r.status_code == 200
    rows = r.get_json()
    assert [row["id"] for row in rows] == [all_day["id"], timed["id"]]
    assert rows[0]["all_day"] is True
    assert rows[0]["start_at"] == target
    assert rows[1]["start_at"] == target + "T14:30"


def test_range_events_hide_private_events_from_other_users(auth_client):
    target = (datetime.now() + timedelta(days=2)).date().isoformat()
    public = _create_event(auth_client, title="Public agenda item", start_at=target + "T09:00")
    private = _create_event(
        auth_client,
        title="Private agenda item",
        start_at=target + "T10:00",
        visibility="private",
    )

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    r = auth_client.get(f"/api/v1/calendar/events?from={target}&to={target}")
    assert r.status_code == 200
    ids = {row["id"] for row in r.get_json()}
    assert public["id"] in ids
    assert private["id"] not in ids


def test_private_calendar_events_are_hidden_from_other_users(auth_client):
    private = _create_event(
        auth_client,
        title="Private prep",
        visibility="private",
        start_at=_future(1),
    )
    public = _create_event(auth_client, title="Internal prep", start_at=_future(1, 1))

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    r = auth_client.get("/api/v1/calendar_events")
    assert r.status_code == 200
    ids = {row["id"] for row in r.get_json()}
    assert public["id"] in ids
    assert private["id"] not in ids

    assert auth_client.get(f"/api/v1/calendar_events/{private['id']}").status_code == 404
    assert auth_client.put(f"/api/v1/calendar_events/{private['id']}/cycle-status").status_code == 404
    assert auth_client.get(f"/api/v1/calendar_events/{private['id']}/comments").status_code == 404
    assert auth_client.post(
        f"/api/v1/calendar_events/{private['id']}/comments",
        json={"body": "should not attach"},
    ).status_code == 404
    assert auth_client.get(f"/api/v1/calendar_events/{private['id']}/activity").status_code == 404
    assert auth_client.delete(f"/api/v1/calendar_events/{private['id']}").status_code == 404

    csv_resp = auth_client.get("/api/v1/calendar_events/export.csv")
    assert csv_resp.status_code == 200
    csv_body = csv_resp.data.decode("utf-8")
    assert "Internal prep" in csv_body
    assert "Private prep" not in csv_body

    r = auth_client.get("/api/v1/calendar/upcoming?days=7")
    assert r.status_code == 200
    titles = {event["title"] for event in r.get_json()["events"]}
    assert "Internal prep" in titles
    assert "Private prep" not in titles


def test_calendar_reminders_surface_due_visible_events(auth_client):
    visible = _create_event(
        auth_client,
        title="Visible reminder",
        start_at=_future(5),
        reminder_date=_future(1),
    )
    _create_event(
        auth_client,
        title="Completed reminder",
        start_at=_future(5),
        reminder_date=_future(1),
        status="done",
    )
    private = _create_event(
        auth_client,
        title="Private reminder",
        start_at=_future(5),
        reminder_date=_future(1),
        visibility="private",
    )

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    r = auth_client.get("/api/v1/calendar/reminders?days=7&limit=10")
    assert r.status_code == 200
    rows = r.get_json()["events"]
    titles = {event["title"] for event in rows}
    assert "Visible reminder" in titles
    assert "Completed reminder" not in titles
    assert "Private reminder" not in titles
    reminder = next(event for event in rows if event["id"] == visible["id"])
    assert reminder["reminder_at"] == visible["reminder_date"]
    assert private["id"] not in {event["id"] for event in rows}


def test_search_finds_calendar_events_and_hides_private(auth_client):
    visible = _create_event(
        auth_client,
        title="Bridge review searchable",
        description="Quarterly phase gate",
        start_at=_future(3),
    )
    private = _create_event(
        auth_client,
        title="Private searchable calendar item",
        visibility="private",
        start_at=_future(3),
    )

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    r = auth_client.get("/api/v1/search?q=searchable")
    assert r.status_code == 200
    rows = r.get_json()
    labels = {row["label"] for row in rows}
    assert "Bridge review searchable" in labels
    assert "Private searchable calendar item" not in labels
    match = next(row for row in rows if row["id"] == visible["id"])
    assert match["source"] == "calendar_events"
    assert match["status"] == "scheduled"
    assert private["id"] not in {row["id"] for row in rows if row["source"] == "calendar_events"}


def test_past_calendar_events_do_not_count_as_overdue(auth_client):
    _create_event(auth_client, title="Past meeting", start_at=_past(1), status="scheduled")

    r = auth_client.get("/api/v1/dashboard")
    assert r.status_code == 200
    bucket = r.get_json()["stats"]["calendar_events"]
    assert bucket["active"] == 1
    assert bucket["overdue"] == 0
    assert bucket["overdue_items"] == []


def test_calendar_update_validates_existing_start_against_new_end(auth_client):
    event = _create_event(auth_client, title="Review", start_at=_future(3))

    r = auth_client.put(
        f"/api/v1/calendar_events/{event['id']}",
        json={"end_at": _future(1)},
    )
    assert r.status_code == 400
    assert "end_at" in r.get_json()["error"]


def test_calendar_update_refreshes_project_fk_from_project_number(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        p1 = Project(project_number="3300.01", name="Original")
        p2 = Project(project_number="3300.02", name="Updated")
        sess.add_all([p1, p2])
        sess.commit()
        p1_id, p2_id = p1.id, p2.id

    event = _create_event(auth_client, project_number="3300.01")
    assert event["project_id"] == p1_id

    r = auth_client.put(
        f"/api/v1/calendar_events/{event['id']}",
        json={"project_number": "3300.02"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["project_number"] == "3300.02"
    assert body["project_id"] == p2_id

def test_dashboard_includes_calendar_surface(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'id="core-loop"' in html
    assert 'id="core-tracker-cards"' in html
    assert 'id="core-review-queue"' in html
    assert 'id="core-overdue"' in html
    assert 'id="core-due-soon"' in html
    assert "function renderCoreOverdue(stats)" in html
    assert "coreDueQueueRows(stats, 'overdue_items', 'Overdue', 'red')" in html
    assert "coreDueQueueRows(stats, 'due_soon_items', 'Due soon', 'amber')" in html
    assert 'class="modal-overlay record-overlay"' in html
    assert '.modal-overlay { display:none; position:fixed; inset:0; background:rgba(22,22,22,0.42); backdrop-filter:blur(2px); z-index:1200;' in html
    assert 'class="modal record-drawer"' in html
    assert 'id="modal-meta"' in html
    assert "renderModalHeader" in html
    assert "renderTrackerSummary" in html
    assert "wireRecordRow" in html
    assert "tracker-summary" in html
    assert "B&amp;R Intake Form" in html
    assert "Upcoming Operations" in html
    assert "Intake Review" in html
    assert 'id="dash-intake-review"' in html
    assert "/intake/review?needs_review=1" in html
    assert "Mark reviewed" in html
    assert "Reminder Queue" in html
    assert 'data-tab="calendar"' in html
    assert 'id="sec-calendar"' in html
    assert 'id="calendar-agenda-view"' in html
    assert 'id="calendar-table-view"' in html
    assert 'id="filter-calendar-window"' in html
    assert 'id="filter-calendar-project"' in html
    assert 'id="filter-calendar-q"' in html
    assert 'Next 30 Days' in html
    assert 'data-calendar-view="agenda"' in html
    assert 'renderCalendarAgenda' in html
    assert 'applyCalendarFilters' in html
    assert 'setupCalendarAllDayControls' in html
    assert 'calendarDatePart' in html
    assert 'tbody-calendar' in html

def test_calendar_routes_require_login(client):
    assert client.get("/api/v1/calendar/upcoming").status_code == 401
    assert client.get("/api/v1/calendar/reminders").status_code == 401
    assert client.get("/api/v1/calendar/events").status_code == 401
