"""OCR parsing for printable intake forms."""
from app.services.ocr_forms import parse_printable_form_ocr


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
FOLLOW_UP_QUESTIONS:
Confirm whether survey has the latest topo.
"""


def test_printable_form_ocr_parser_detects_project_work_form():
    parsed = parse_printable_form_ocr(PROJECT_WORK_OCR, source_ref="rm-page-12")

    assert parsed["detected"] is True
    assert parsed["form_id"] == "TT-PROJECT-WORK-REQUEST"
    assert parsed["target_table"] == "project_work_tasks"
    assert parsed["capture_target"] == "project_work_tasks"
    assert parsed["source"] == "paper-form"
    assert parsed["project_number"] == "2301.04"
    assert parsed["requested_by"] == "Mike from Survey"
    assert parsed["priority"] == "High"
    assert parsed["due_date"] == "2026-06-03"
    assert "grading plan revised" in parsed["request_summary"]
    assert "C-301" in parsed["requested_action"]
    assert parsed["confidence"] >= 0.9

    prefill = parsed["prefill"]
    assert prefill["target"] == "project_work_tasks"
    assert prefill["source"] == "paper-form"
    assert prefill["project_number"] == "2301.04"
    assert prefill["requested_by"] == "Mike from Survey"
    assert prefill["due_date"] == "2026-06-03"
    assert "Source ref: rm-page-12" in prefill["text"]
    assert "Original OCR text:" in prefill["text"]


def test_printable_form_ocr_parser_routes_general_follow_up_to_capture_fallback():
    parsed = parse_printable_form_ocr("""FORM_ID: TT-GENERAL-FOLLOW-UP
TARGET_TABLE: personal_items
REQUESTOR: Office Manager
REQUEST_SUMMARY:
Ask about the plotter maintenance schedule.
""")

    assert parsed["detected"] is True
    assert parsed["target_table"] == "personal_items"
    assert parsed["capture_target"] == "work_tasks"
    assert parsed["prefill"]["target"] == "work_tasks"
    assert parsed["warnings"]


def test_printable_form_ocr_parser_handles_generic_ocr_text():
    parsed = parse_printable_form_ocr("Please fix the CAD label on project 2301.04 by Friday.")

    assert parsed["detected"] is False
    assert parsed["capture_target"] == "work_tasks"
    assert parsed["source"] == "remarkable-ocr"
    assert parsed["project_number"] == "2301.04"
    assert "No known TaskTrack FORM_ID" in parsed["warnings"][0]


def test_ocr_parse_api_requires_login(client):
    r = client.post("/api/v1/intake/ocr/parse", json={"text": PROJECT_WORK_OCR})
    assert r.status_code in (302, 401)


def test_ocr_parse_api_returns_prefill(auth_client):
    r = auth_client.post("/api/v1/intake/ocr/parse", json={
        "text": PROJECT_WORK_OCR,
        "source_ref": "scan-001.pdf",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["form_title"] == "Project Work Request"
    assert body["prefill"]["target"] == "project_work_tasks"
    assert body["prefill"]["source"] == "paper-form"
    assert body["prefill"]["project_number"] == "2301.04"
    assert "scan-001.pdf" in body["prefill"]["text"]


def test_ocr_capture_page_exposes_parser(auth_client):
    r = auth_client.get("/capture/ocr")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Parse OCR Form" in html
    assert "/api/v1/intake/ocr/parse" in html
    assert "parseOcrForm" in html
    assert "paper-form" in html
    assert "Parsed Intake Form" in html


def test_dashboard_capture_accepts_paper_form_prefill(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Paper / printable form" in html
    assert "values.due_date" in html
