"""Phase-4 Kanban view smoke tests.

Kanban renders client-side from cached data, so the only server-side
verification is that the dashboard template ships the view-toggle
markers and the project-tasks endpoint still returns 200. Lane
bucketing + sort order are exercised by the JS at runtime.
"""


def test_dashboard_includes_kanban_markup(auth_client):
    """Dashboard HTML must carry the Kanban view containers + toggle
    buttons so the JS can find them on tab activation."""
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'id="project-table-view"' in html
    assert 'id="project-kanban-view"' in html
    assert 'id="kanban-project"' in html
    assert 'data-view="table"' in html
    assert 'data-view="kanban"' in html


def test_dashboard_includes_paper_ocr_capture_affordance(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "tablet OCR text" in html
    assert "reMarkable / OCR note" in html
    assert "remarkable-ocr" in html
    assert 'id="filter-triage-source"' in html
    assert "All Sources" in html
    assert "captureSource" in html
    assert "applyCapturePresetRoute" in html


def test_project_endpoint_still_returns_200(auth_client):
    """Kanban reuses GET /api/v1/project_work_tasks — no new endpoint."""
    # Pre-seed one task so we exercise the rendered branch.
    auth_client.post("/api/v1/project_work_tasks", json={
        "project_name": "Bridge work",
        "title": "Survey check",
        "project_number": "0042.00",
        "task_description": "Verify",
    })
    r = auth_client.get("/api/v1/project_work_tasks")
    assert r.status_code == 200
    rows = r.get_json()
    assert isinstance(rows, list)
    # Cover all four lanes so the JS bucketing has something to do at
    # runtime; the assertion here is just that the rows come through.
    assert any(r2["project_number"] == "0042.00" for r2 in rows)


def test_kanban_no_new_route_introduced(auth_client):
    """No /api/v1/kanban/* — the toggle is purely visual."""
    r = auth_client.get("/api/v1/kanban/project")
    assert r.status_code == 404
