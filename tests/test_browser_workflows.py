"""Browser-first workflow helper pages."""


def test_ocr_capture_page_requires_login(client):
    assert client.get("/capture/ocr", follow_redirects=False).status_code == 302


def test_ocr_capture_page_renders(auth_client):
    r = auth_client.get("/capture/ocr")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "reMarkable / OCR Capture" in html
    assert "Send To Dashboard Capture" in html
    assert "tasktrack-capture-prefill" in html
    assert "capture_source" in html
    assert "capture_target" in html


def test_testing_checklist_page_requires_login(client):
    assert client.get("/testing", follow_redirects=False).status_code == 302


def test_testing_checklist_page_renders(auth_client):
    r = auth_client.get("/testing")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Browser Testing Checklist" in html
    assert "Capture Loop" in html
    assert "Calendar And Meetings" in html
    assert "Projects And Map" in html
    assert "tasktrack-browser-test-checks-v1" in html
    assert "/capture/ocr" in html
