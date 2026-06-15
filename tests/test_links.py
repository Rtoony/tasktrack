"""In-process tests for links blueprint + smart-link recognizer.

Also guards the SPA wiring for the Links panel (WORK_PLAN P2-4). The
backend + recognizer shipped and were tested first; the panel UI lives
entirely inside the server-rendered ``index.html`` SPA. The
render-contract tests below fail loudly if a refactor drops the panel,
unhooks ``loadLinks`` from the modal, or attaches the ``links`` field to
a tab whose API_MAP table the links API would reject — i.e. a Links
panel that can never load.
"""
import re

import pytest

from app.config import ALLOWED_TABLES
from app.db import get_session
from app.models import ActivityLog, CalendarEvent, Link, WorkTask
from app.services import links as link_svc


def _login(client):
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["user_name"] = "Tester"
        s["user_role"] = "user"


def _seed_work_task(temp_app, title="Seed task") -> int:
    with temp_app.app_context():
        sess = get_session()
        wt = WorkTask(title=title)
        sess.add(wt)
        sess.commit()
        return wt.id


# ── Recognizer unit tests (pure, no Flask) ──────────────────────────────

@pytest.mark.parametrize("url,expected_kind,expected_label_contains", [
    ("https://paperless.roonytoony.dev/documents/42", "paperless", "Paperless doc #42"),
    ("https://portal.roonytoony.dev/calendar", "calendar", "Calendar"),
    ("https://portal.roonytoony.dev/calendar/2026-05-04", "calendar", "2026-05-04"),
    ("https://portal.roonytoony.dev/dashboard", "portal", "dashboard"),
    ("https://github.com/Rtoony/tasktrack/pull/12", "github_pr", "PR Rtoony/tasktrack#12"),
    ("https://github.com/Rtoony/tasktrack/issues/7", "github_issue", "Issue Rtoony/tasktrack#7"),
    ("https://github.com/Rtoony/tasktrack", "github_repo", "Rtoony/tasktrack"),
    ("https://t.me/MyTrack_Tasks_Bot/123", "telegram", "@MyTrack_Tasks_Bot msg 123"),
    ("https://example.com/foo/bar", "generic", "example.com/bar"),
])
def test_recognizer_labels_known_hosts(url, expected_kind, expected_label_contains):
    rec = link_svc._recognize(url)
    assert rec.source_kind == expected_kind
    assert expected_label_contains in rec.label


# ── Route tests ─────────────────────────────────────────────────────────

def test_list_requires_auth(client):
    r = client.get("/api/v1/links/work_tasks/1")
    assert r.status_code in (401, 302)


def test_list_rejects_unknown_table(client):
    _login(client)
    r = client.get("/api/v1/links/nope/1")
    assert r.status_code == 400


def test_list_404_when_record_missing(client):
    _login(client)
    r = client.get("/api/v1/links/work_tasks/999")
    assert r.status_code == 404


def test_add_list_delete_roundtrip(client, temp_app):
    _login(client)
    record_id = _seed_work_task(temp_app)

    # Add a Paperless link — recognizer should label it.
    r = client.post(
        f"/api/v1/links/work_tasks/{record_id}",
        json={"url": "https://paperless.roonytoony.dev/documents/42"},
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["source_kind"] == "paperless"
    assert body["label"] == "Paperless doc #42"
    link_id = body["id"]

    # List shows it.
    r = client.get(f"/api/v1/links/work_tasks/{record_id}")
    assert len(r.get_json()) == 1

    # Activity log got an entry.
    with temp_app.app_context():
        sess = get_session()
        actions = [a.action for a in sess.query(ActivityLog).all()]
        assert "link_added" in actions

    # Delete returns 204; row is gone.
    r = client.delete(f"/api/v1/links/{link_id}")
    assert r.status_code == 204
    with temp_app.app_context():
        sess = get_session()
        assert sess.get(Link, link_id) is None
        assert "link_removed" in [a.action for a in sess.query(ActivityLog).all()]




def test_private_calendar_links_hidden_from_non_owner(client, temp_app):
    _login(client)
    with temp_app.app_context():
        sess = get_session()
        event = CalendarEvent(
            title="Private link event",
            event_type="meeting",
            start_at="2026-05-27T09:00",
            visibility="private",
            created_by_user_id=1,
        )
        sess.add(event)
        sess.flush()
        link = Link(
            table_name="calendar_events",
            record_id=event.id,
            url="https://example.com/private",
            label="Private link",
        )
        sess.add(link)
        sess.commit()
        event_id = event.id
        link_id = link.id

    with client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    assert client.get(f"/api/v1/links/calendar_events/{event_id}").status_code == 404
    assert client.post(
        f"/api/v1/links/calendar_events/{event_id}",
        json={"url": "https://example.com/other"},
    ).status_code == 404
    assert client.delete(f"/api/v1/links/{link_id}").status_code == 404


def test_add_dedupes_same_url(client, temp_app):
    _login(client)
    record_id = _seed_work_task(temp_app)
    url = "https://github.com/Rtoony/tasktrack/pull/1"
    for _ in range(3):
        r = client.post(f"/api/v1/links/work_tasks/{record_id}", json={"url": url})
        assert r.status_code == 201
    with temp_app.app_context():
        sess = get_session()
        assert sess.query(Link).count() == 1


def test_user_label_overrides_recognizer(client, temp_app):
    _login(client)
    record_id = _seed_work_task(temp_app)
    r = client.post(
        f"/api/v1/links/work_tasks/{record_id}",
        json={"url": "https://paperless.roonytoony.dev/documents/9", "label": "Survey markup"},
    )
    assert r.status_code == 201
    body = r.get_json()
    assert body["label"] == "Survey markup"
    assert body["source_kind"] == "paperless"  # recognizer still tags the kind


@pytest.mark.parametrize("bad_url,reason", [
    ("", "required"),
    ("not-a-url", "http"),
    ("ftp://example.com", "http"),
    ("https://", "host"),
    ("https://" + "x" * 3000, "2048"),
])
def test_add_rejects_bad_urls(client, temp_app, bad_url, reason):
    _login(client)
    record_id = _seed_work_task(temp_app)
    r = client.post(f"/api/v1/links/work_tasks/{record_id}", json={"url": bad_url})
    assert r.status_code == 400
    assert reason.lower() in r.get_json()["error"].lower()


# ── SPA wiring contract (Links panel UI lives in index.html) ────────────
#
# The panel is server-rendered SPA markup + JS, so the only in-process way
# to assert it's actually exposed is to render the page and inspect it.

def _render_spa(auth_client) -> str:
    r = auth_client.get("/")
    assert r.status_code == 200, r.data[:200]
    return r.get_data(as_text=True)


def test_spa_renders_links_panel_machinery(auth_client):
    """The core panel pieces must all be present, or the Links UI is dead
    code even though the backend works."""
    html = _render_spa(auth_client)
    # The form-builder branch that creates the panel container.
    assert "f.type==='links'" in html
    assert "lnk-panel" in html
    # The loader that fetches /api/v1/links and renders rows + the add box.
    assert "async function loadLinks(" in html
    assert "/api/v1/links/" in html
    # The modal must invoke the loader, else the panel never populates.
    assert "loadLinks(table,editingId)" in html
    # Remove control wired to DELETE.
    assert "method:'DELETE'" in html


def _spa_link_tabs(html: str) -> list[str]:
    """Tabs whose FORMS definition includes a {type:'links'} field.

    Anchored to the ``const FORMS = {`` object so it can't latch onto the
    unrelated STATUS_FLOWS / empty-state blocks that share the same
    ``<tab>:[`` shape earlier in the file. For each tab, check whether a
    ``type:'links'`` field appears before the next top-level tab key.
    """
    forms_start = html.index("const FORMS = {")
    forms = html[forms_start:]

    tab_keys = ["work", "project", "training", "personnel",
                "calendar", "triage"]
    found = []
    for key in tab_keys:
        m = re.search(rf"\n  {key}:\[", forms)
        if not m:
            continue
        rest = forms[m.end():]
        # Stop at the next two-space-indented tab key or the FORMS close.
        nxt = re.search(r"\n  [a-z_]+:\[|\n};", rest)
        block = rest[: nxt.start()] if nxt else rest
        if "type:'links'" in block:
            found.append(key)
    return found


def test_links_panel_attached_to_every_tracker_form(auth_client):
    """Every primary tracker tab should expose the Links panel — losing one
    silently is exactly the kind of scope-creep regression to catch."""
    html = _render_spa(auth_client)
    tabs = _spa_link_tabs(html)
    for expected in ("work", "project", "training", "personnel",
                     "calendar", "triage"):
        assert expected in tabs, f"Links panel missing from '{expected}' form"


def test_link_form_tabs_map_to_links_api_allowed_tables():
    """Contract between the SPA's API_MAP and the links blueprint: every
    table a Links-bearing tab posts to must be accepted by the links API
    (which gates on ALLOWED_TABLES). A mismatch = a panel that 400s on
    load. API_MAP is duplicated here intentionally so a drift in either
    side trips the test."""
    api_map = {
        "work": "work_tasks",
        "project": "project_work_tasks",
        "training": "training_tasks",
        "personnel": "personnel_issues",
        "triage": "inbox_items",
        "calendar": "calendar_events",
        "personal": "personal_items",
    }
    for tab, table in api_map.items():
        assert table in ALLOWED_TABLES, (
            f"tab '{tab}' maps to '{table}', which the links API would reject"
        )


def test_api_map_in_spa_matches_test_expectation(auth_client):
    """Guard the duplicated API_MAP above against SPA drift: assert the
    real mapping entries are present in the rendered source."""
    html = _render_spa(auth_client)
    for tab, table in (
        ("work", "work_tasks"),
        ("project", "project_work_tasks"),
        ("calendar", "calendar_events"),
        ("triage", "inbox_items"),
    ):
        assert f"{tab}:'{table}'" in html, f"API_MAP drifted for '{tab}'"
