"""Sendable/PDF-style intake form tests."""
from sqlalchemy import select

from app.db import get_session
from app.models import PersonalItem, ProjectWorkTask, TrainingTask, WorkTask


def test_hub_lists_practical_sendable_forms(client):
    r = client.get("/intake")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Project Work Request" in html
    assert "/intake/project-request" in html
    assert "General Follow-Up" in html
    assert "/intake/general-follow-up" in html
    assert "Submit CAD changes, fixes, or manager follow-up requests" in html
    assert "Copy Link" in html
    assert "http://localhost/intake/project-request" in html


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
    assert "Open Record" in html
    assert "row-detail" in html
    assert "row.detail" in html
    assert "/reports/intake" in html
    assert "/api/v1/reports/intake.csv" in html


def test_project_request_form_renders_pdf_style(client):
    r = client.get("/intake/project-request")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "TT-WEB-PROJECT-WORK-REQUEST" in html
    assert "Print / Save PDF" in html
    assert "Copy Form Link" in html
    assert "Share link" in html
    assert "http://localhost/intake/project-request" in html
    assert "Project Number" in html
    assert "What needs to be done?" in html
    assert "request form" in html


def test_project_request_submission_creates_reviewable_project_task(client, temp_app):
    r = client.post("/intake/project-request", data={
        "title": "Revise grading exhibit",
        "project_number": "2301.04",
        "project_name": "Condo Castle",
        "engineer": "Mike from Survey",
        "billing_phase": "01",
        "task_description": "Update the grading exhibit before the agency meeting.",
        "due_at": "2026-06-05T15:00",
        "priority": "High",
    })
    assert r.status_code == 200
    assert "Project request submitted successfully" in r.get_data(as_text=True)

    with temp_app.app_context():
        sess = get_session()
        row = sess.scalar(select(ProjectWorkTask).where(ProjectWorkTask.title == "Revise grading exhibit"))
        assert row is not None
        assert row.project_number == "2301.04"
        assert row.project_name == "Condo Castle"
        assert row.engineer == "Mike from Survey"
        assert row.source == "web-form"
        assert row.needs_review == 1
        assert row.priority == "High"


def test_general_followup_submission_creates_internal_item(client, temp_app):
    r = client.post("/intake/general-follow-up", data={
        "title": "Check plotter maintenance",
        "category": "Office",
        "body": "Confirm next service date and who owns supplies.",
        "priority": "Medium",
        "due_date": "2026-06-10",
        "source_ref": "Office meeting",
    })
    assert r.status_code == 200
    assert "Follow-up request submitted successfully" in r.get_data(as_text=True)

    with temp_app.app_context():
        sess = get_session()
        row = sess.scalar(select(PersonalItem).where(PersonalItem.title == "Check plotter maintenance"))
        assert row is not None
        assert row.category == "Office"
        assert row.source == "web-form"
        assert row.source_ref == "Office meeting"
        assert row.needs_review == 1
        assert row.due_date == "2026-06-10"


def test_cad_and_training_forms_tag_source_and_review(client, temp_app):
    cad = client.post("/intake/cad-development", data={
        "title": "Fix sheet labels",
        "requested_by": "PM",
        "cad_skill_area": "Sheet production",
        "description": "Sheet labels need cleanup.",
        "request_reference": "Email from PM",
        "due_date": "2026-06-11",
    })
    assert cad.status_code == 200

    training = client.post("/intake/training", data={
        "title": "Bluebeam markup refresher",
        "requested_by": "PM",
        "trainees": "CAD Team",
        "skill_area": "Bluebeam",
        "training_goals": "Reduce missed markup comments.",
        "additional_context": "Repeated issue in reviews.",
        "due_date": "2026-06-12",
    })
    assert training.status_code == 200

    with temp_app.app_context():
        sess = get_session()
        cad_row = sess.scalar(select(WorkTask).where(WorkTask.title == "Fix sheet labels"))
        training_row = sess.scalar(select(TrainingTask).where(TrainingTask.title == "Bluebeam markup refresher"))
        assert cad_row is not None
        assert cad_row.source == "web-form"
        assert cad_row.needs_review == 1
        assert training_row is not None
        assert training_row.source == "web-form"
        assert training_row.needs_review == 1


def test_general_followup_can_be_marked_reviewed(auth_client, temp_app):
    r = auth_client.post("/intake/general-follow-up", data={
        "title": "Review office intake",
        "category": "Office",
        "body": "Confirm this came through the intake review queue.",
        "priority": "High",
    })
    assert r.status_code == 200

    with temp_app.app_context():
        sess = get_session()
        row = sess.scalar(select(PersonalItem).where(PersonalItem.title == "Review office intake"))
        assert row is not None
        row_id = row.id
        assert row.needs_review == 1

    confirmed = auth_client.post(f"/api/v1/personal_items/{row_id}/confirm", json={})
    assert confirmed.status_code == 200
    assert confirmed.get_json()["needs_review"] == 0

    report = auth_client.get("/api/v1/reports/intake?needs_review=1")
    assert report.status_code == 200
    assert "Review office intake" not in str(report.get_json())
