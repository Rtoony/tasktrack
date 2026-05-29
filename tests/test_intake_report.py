"""Intake source report tests."""
from app.db import get_session
from app.models import WorkTask

PROJECT_WORK_OCR = """FORM_ID: TT-PROJECT-WORK-REQUEST
TARGET_TABLE: project_work_tasks
SOURCE: paper-form
REQUESTOR: Mike from Survey
PROJECT_NUMBER: 230104
PRIORITY: High
DUE_DATE: 06/03/2026
REQUEST_SUMMARY:
Need the grading plan revised for the storm tie-in.
REQUESTED_ACTION:
Update sheets C-301 through C-305 and send a redline for review.
"""


def _create_ocr_item(client):
    r = client.post("/api/v1/intake/ocr/create", json={"text": PROJECT_WORK_OCR})
    assert r.status_code == 201
    return r.get_json()["created"]




def test_intake_source_report_defaults_include_web_forms(auth_client):
    r = auth_client.post("/intake/project-request", data={
        "title": "Default report web form item",
        "project_number": "2301.04",
        "project_name": "Condo Castle",
        "engineer": "PM",
        "task_description": "Review this web form in the intake report.",
        "priority": "High",
    })
    assert r.status_code == 200

    report = auth_client.get("/api/v1/reports/intake")
    assert report.status_code == 200
    body = report.get_json()
    assert "web-form" in body["filters"]["sources"]
    assert body["summary"]["by_source"]["web-form"] == 1
    assert any(row["title"] == "Default report web form item" for row in body["rows"])


def test_intake_source_report_json_and_review_filter(auth_client, temp_app):
    created = _create_ocr_item(auth_client)
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(
            title="Freeform tablet note",
            source="remarkable-ocr",
            status="Not Started",
            priority="Medium",
            needs_review=0,
            ai_raw_input="tablet note",
        ))
        sess.commit()

    r = auth_client.get("/api/v1/reports/intake?sources=paper-form,remarkable-ocr&days=30")
    assert r.status_code == 200
    body = r.get_json()
    assert body["summary"]["count"] == 2
    assert body["summary"]["needs_review_count"] == 1
    assert body["summary"]["by_source"]["paper-form"] == 1
    assert body["summary"]["by_source"]["remarkable-ocr"] == 1
    assert any(row["table"] == created["table"] and row["id"] == created["id"] for row in body["rows"])

    review = auth_client.get("/api/v1/reports/intake?sources=paper-form,remarkable-ocr&needs_review=1")
    assert review.status_code == 200
    review_body = review.get_json()
    assert review_body["summary"]["count"] == 1
    assert review_body["rows"][0]["needs_review"] is True


def test_intake_source_report_csv_and_html(auth_client):
    _create_ocr_item(auth_client)

    csv_r = auth_client.get("/api/v1/reports/intake.csv?sources=paper-form")
    assert csv_r.status_code == 200
    csv_text = csv_r.get_data(as_text=True)
    assert "project_work_tasks" in csv_text
    assert "paper-form" in csv_text
    assert "record_url" in csv_text.splitlines()[0]

    html_r = auth_client.get("/reports/intake?sources=paper-form")
    assert html_r.status_code == 200
    html = html_r.get_data(as_text=True)
    assert "Intake Source Report" in html
    assert "Need the grading plan revised" in html
    assert "/api/v1/reports/intake.csv" in html


def test_report_center_and_admin_link_to_intake_report(auth_client, admin_client):
    reports = auth_client.get("/reports")
    assert reports.status_code == 200
    assert "/reports/intake" in reports.get_data(as_text=True)

    admin = admin_client.get("/admin")
    assert admin.status_code == 200
    assert "/reports/intake" in admin.get_data(as_text=True)
