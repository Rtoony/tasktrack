"""Mobile companion shell contract tests.

These are server-side template/API checks. The interaction code still runs
client-side, but these assertions keep the mobile-specific hooks, session API
paths, and no-token contract from silently regressing.
"""


def _root_html(auth_client) -> str:
    response = auth_client.get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _script_slice(html: str, start: str, end: str) -> str:
    start_at = html.index(start)
    end_at = html.index(end, start_at)
    return html[start_at:end_at]


def test_mobile_shell_renders_capture_today_review_contract(auth_client):
    html = _root_html(auth_client)

    assert 'id="mobile-companion-shell"' in html
    assert 'id="mobile-pane-capture"' in html
    assert 'id="mobile-pane-today"' in html
    assert 'id="mobile-pane-review"' in html
    assert 'data-mobile-pane-btn="capture"' in html
    assert 'data-mobile-pane-btn="today"' in html
    assert 'data-mobile-pane-btn="review"' in html
    assert "@media (max-width:639px)" in html
    assert ".container:has(.mobile-companion-shell) > .section" in html
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in html


def test_mobile_quick_capture_uses_session_inbox_api_without_client_token(auth_client):
    html = _root_html(auth_client)

    assert 'id="mobile-quick-text"' in html
    assert 'id="mobile-quick-submit"' in html
    assert '<meta name="csrf-token"' in html
    assert "headers: { 'Content-Type':'application/json', 'X-CSRF-Token': CSRF_TOKEN }" in html
    assert "key:'capture-card', slot:'mobile-slot-capture'" not in html
    assert "source: 'web-mobile'" in html
    assert "api('POST', '/api/v1/inbox'" in html

    quick_capture = _script_slice(
        html,
        "async function submitMobileQuickCapture()",
        "function clearMobileQuickCapture",
    )
    assert "Authorization" not in quick_capture
    assert "X-TaskTrack-Token" not in quick_capture
    assert "TASKTRACK_TOKEN" not in quick_capture
    assert "/api/maximus" not in quick_capture


def test_mobile_today_can_mark_internal_items_done(auth_client):
    html = _root_html(auth_client)

    assert 'id="mobile-today-list"' in html
    assert "const MOBILE_TODAY_INTERNAL_TABS" in html
    assert "async function markMobileTodayDone" in html
    assert "api('PUT', '/api/v1/personal_items/' + row.id, { status:'Done' })" in html
    assert "Mark quick wins done without opening the desktop table." in html


def test_mobile_review_queue_confirms_needs_review_trackers(auth_client):
    html = _root_html(auth_client)

    assert 'id="mobile-review-list"' in html
    assert "const MOBILE_REVIEW_TRACKERS" in html
    assert "table:'work_tasks'" in html
    assert "table:'project_work_tasks'" in html
    assert "table:'training_tasks'" in html
    assert "table:'personal_items'" in html
    assert "async function confirmMobileReviewRow" in html
    assert "'/api/v1/' + row._mobileTable + '/' + row.id + '/confirm'" in html
    assert "editRecord(row._mobileTab, row.id)" in html


def test_mobile_completion_endpoint_accepts_session_update(auth_client):
    created = auth_client.post(
        "/api/v1/personal_items",
        json={
            "title": "Mobile follow-up",
            "category": "Follow-up",
            "status": "New",
            "priority": "Medium",
        },
    )
    assert created.status_code == 201, created.data
    item_id = created.get_json()["id"]

    updated = auth_client.put(
        f"/api/v1/personal_items/{item_id}",
        json={"status": "Done"},
    )

    assert updated.status_code == 200, updated.data
    assert updated.get_json()["status"] == "Done"
