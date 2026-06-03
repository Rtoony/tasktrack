"""In-process tests for the bot-scoped /api/v1/agenda endpoint."""
import importlib
from datetime import date, datetime, timedelta

import pytest

from app.db import get_session
from app.models import CalendarEvent

BOT_TOKEN = "test-bot-token"


@pytest.fixture
def with_bot_token(monkeypatch):
    monkeypatch.setenv("TASKTRACK_TOKEN_BOT", BOT_TOKEN)
    from app import tokens
    importlib.reload(tokens)
    yield
    importlib.reload(tokens)


def test_agenda_requires_bot_token(client):
    assert client.get("/api/v1/agenda").status_code == 401


def test_agenda_includes_tasktrack_event(client, temp_app, with_bot_token):
    soon = (datetime.combine(date.today(), datetime.min.time())
            + timedelta(days=2, hours=9))
    with temp_app.app_context():
        sess = get_session()
        sess.add(CalendarEvent(
            title="CAD review", event_type="meeting",
            start_at=soon.strftime("%Y-%m-%dT%H:%M"),
            end_at=(soon + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            status="scheduled", visibility="internal", all_day=0,
        ))
        sess.commit()

    r = client.get("/api/v1/agenda", headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    body = r.get_json()
    titles = {e["title"]: e for e in body["events"]}
    assert "CAD review" in titles
    assert titles["CAD review"]["source"] == "tasktrack"
    assert body["counts"]["by_source"].get("tasktrack", 0) >= 1


def test_agenda_excludes_cancelled(client, temp_app, with_bot_token):
    soon = (datetime.combine(date.today(), datetime.min.time())
            + timedelta(days=1, hours=9))
    with temp_app.app_context():
        sess = get_session()
        sess.add(CalendarEvent(
            title="Cancelled thing", event_type="meeting",
            start_at=soon.strftime("%Y-%m-%dT%H:%M"),
            status="cancelled", visibility="internal", all_day=0,
        ))
        sess.commit()
    r = client.get("/api/v1/agenda", headers={"X-Token": BOT_TOKEN})
    assert "Cancelled thing" not in {e["title"] for e in r.get_json()["events"]}


def test_agenda_reads_radicale_ics(client, temp_app, with_bot_token, tmp_path, monkeypatch):
    coll = tmp_path / "collections" / "collection-root" / "rtoony" / "work"
    coll.mkdir(parents=True)
    day = (date.today() + timedelta(days=3)).strftime("%Y%m%d")
    (coll / "e.ics").write_text(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
        "SUMMARY:Radicale Site Walk\n"
        f"DTSTART:{day}T100000Z\nDTEND:{day}T110000Z\n"
        "LOCATION:Field\nUID:rad-test@x\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )
    monkeypatch.setattr("app.routes.agenda.RADICALE_ROOT", tmp_path / "collections")

    r = client.get("/api/v1/agenda", headers={"X-Token": BOT_TOKEN})
    assert r.status_code == 200, r.data
    body = r.get_json()
    rad = [e for e in body["events"] if e["title"] == "Radicale Site Walk"]
    assert rad, body["events"]
    assert rad[0]["source"] == "radicale:work"
    assert rad[0]["location"] == "Field"
