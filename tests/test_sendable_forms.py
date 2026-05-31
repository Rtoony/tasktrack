"""Unified B&R intake form tests."""
from app.db import get_session
from app.models import InboxItem, Project, ProjectWorkTask


def _submit_project_work(auth_client):
    return auth_client.post("/api/v1/intake/submit", json={
        "type": "project_work",
        "fields": {
            "summary": "Revise grading exhibit",
            "project": "2301.04",
            "phase": "200 - Prelim Design",
            "details": "Update the grading exhibit before the agency meeting.",
        },
        "priority": "High",
        "desired_by": "2026-06-05",
    })


def test_hub_lists_unified_request_links(client):
    r = client.get("/intake")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Project Work Request" in html
    assert "/intake/request?type=project_work" in html
    assert "General Follow-Up" in html
    assert "/intake/request?type=general" in html
    assert "Submit CAD changes, fixes, or manager follow-up requests" in html
    assert "Copy Link" in html
    assert "http://localhost/intake/request?type=project_work" in html


def test_intake_review_queue_requires_login(client):
    unauth = client.get("/intake/review")
    assert unauth.status_code == 302
    assert "/login" in unauth.headers["Location"]


def test_intake_review_queue_renders_for_authenticated_user(auth_client):
    r = auth_client.get("/intake/review?needs_review=1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Intake Review Queue" in html
    assert "/api/v1/reports/intake" in html
    assert "Mark reviewed" in html
    assert "CONFIRM_TABLES.has(row.table)" in html
    assert "row.table === 'inbox_items'" in html
    assert "triage required" in html
    assert "Open Record" in html
    assert "row-detail" in html
    assert "row.detail" in html
    assert "Quick presets" in html
    assert "Paper / OCR" in html
    assert "--bg:#f4f4f4" in html
    assert "--accent:#0f62fe" in html
    assert "/intake/review?sources=paper-form,remarkable-ocr&needs_review=1&days=30&limit=100" in html
    assert "All Intake" in html
    assert "/reports/intake" in html
    assert "/api/v1/reports/intake.csv" in html


def test_unified_request_form_requires_login(client):
    r = client.get("/intake/request", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_unified_request_form_renders_br_shell(auth_client):
    r = auth_client.get("/intake/request?type=cad&project=1588.01")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Submit a Request" in html
    assert "Brelje &amp; Race" in html
    assert "window.TT_INTAKE" in html
    assert "/api/v1/intake/submit" in html
    assert "/api/v1/projects/search" in html
    assert "breljerace-logo-white.png" in html
    assert "js/br-intake.bundle.js" in html
    assert "unpkg.com" not in html
    assert "text/babel" not in html
    assert "sk-" not in html.lower()


def test_project_search_returns_active_matches(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="1588.01", name="Reservoir Upgrade", client="City"))
        sess.add(Project(project_number="9999.00", name="Hidden", client="Dormant", active=0))
        sess.commit()

    r = auth_client.get("/api/v1/projects/search?q=reservoir")
    assert r.status_code == 200
    body = r.get_json()
    assert body == [{
        "project_number": "1588.01",
        "name": "Reservoir Upgrade",
        "client": "City",
    }]


def test_unified_submit_creates_reviewable_inbox_item(auth_client, temp_app):
    r = _submit_project_work(auth_client)
    assert r.status_code == 201
    payload = r.get_json()
    assert payload["ref"].startswith("INT-")

    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, payload["inbox_id"])
        assert item is not None
        assert item.title == "Revise grading exhibit"
        assert item.source == "web-form"
        assert item.source_ref == payload["ref"]
        assert item.status == "New"
        assert item.priority == "High"
        assert item.due_date == "2026-06-05"
        assert item.created_by_name == "Tester"
        assert "suggested_target" in item.body
        assert "project_work_tasks" in item.body

    report = auth_client.get("/api/v1/reports/intake?needs_review=1")
    assert report.status_code == 200
    body = report.get_json()
    assert body["summary"]["by_table"]["inbox_items"] == 1
    assert body["summary"]["needs_review_count"] == 1
    assert body["rows"][0]["title"] == "Revise grading exhibit"


def test_inbox_item_can_accept_attachments_and_promote(auth_client, temp_app):
    created = _submit_project_work(auth_client).get_json()
    inbox_id = created["inbox_id"]

    missing_file = auth_client.post(f"/api/v1/attachments/inbox_items/{inbox_id}", data={})
    assert missing_file.status_code == 400
    assert "Missing 'file'" in missing_file.get_json()["error"]

    promoted = auth_client.post(f"/api/v1/inbox/{inbox_id}/promote", json={
        "target_table": "project_work_tasks",
        "overrides": {
            "project_name": "Condo Castle",
            "project_number": "2301.04",
            "task_description": "Update the grading exhibit before the agency meeting.",
        },
    })
    assert promoted.status_code == 201
    record_id = promoted.get_json()["promoted_to"]["id"]

    with temp_app.app_context():
        sess = get_session()
        item = sess.get(InboxItem, inbox_id)
        task = sess.get(ProjectWorkTask, record_id)
        assert item.status == "Archived"
        assert item.promoted_to_table == "project_work_tasks"
        assert task.title == "Revise grading exhibit"
        assert task.project_number == "2301.04"


def test_submit_validation_rejects_missing_required_fields(auth_client):
    r = auth_client.post("/api/v1/intake/submit", json={
        "type": "project_work",
        "fields": {"summary": "Missing project"},
    })
    assert r.status_code == 400
    assert r.get_json()["fields"] == ["project"]


def test_legacy_intake_routes_redirect_to_unified_form(auth_client):
    expected = {
        "/intake/project-request": "type=project_work",
        "/intake/project-work": "type=project_work",
        "/intake/cad-development": "type=cad",
        "/intake/training": "type=training",
        "/intake/general-follow-up": "type=general",
        "/intake/incident": "type=problem",
    }
    for path, marker in expected.items():
        r = auth_client.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert "/intake/request" in r.headers["Location"]
        assert marker in r.headers["Location"]
