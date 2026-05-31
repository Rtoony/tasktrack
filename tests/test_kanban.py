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
    assert 'kanban-card-actions' in html
    assert 'kanban-card-action' in html
    assert 'openProjectWorkspaceSmart(r.project_id, r.project_number)' in html
    assert 'openProjectReportSmart(r.project_id, r.project_number)' in html


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
    assert "capture-preset-btn" in html
    assert "Meeting Notes" in html
    assert "CAD Issue" in html
    assert "Training Need" in html
    assert "CAPTURE_PREFILL_KEY" in html


def test_dashboard_uses_left_rail_shell(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'class="app-shell' in html
    assert 'class="side-nav"' in html
    assert 'id="shell-context">/ Dashboard</span>' in html
    assert 'class="tabs side-nav-list"' in html
    assert 'data-tab="work" data-shell-title="CAD Development"' in html
    assert 'function updateShellContext(title)' in html
    assert 'updateShellContext(tabTitleForButton(btn));' in html
    assert 'position:static; border-right:none; border-bottom:1px solid var(--border); overflow-x:auto;' in html
    assert 'width:min(360px, calc(100vw - 1.7rem));' in html
    assert '<span class="tab-divider-label">Work</span>' in html
    assert '<span class="tab-divider-label">Context</span>' in html
    assert '<span class="tab-divider-label">Flow</span>' in html
    assert '<span class="tab-divider-label">Output</span>' in html
    assert 'class="side-link" href="/intake"' in html
    assert 'class="side-link" href="/reports"' in html
    assert 'class="side-link" href="/weekly"' in html
    header_at = html.index('<header class="header">')
    shell_at = html.index('class="app-shell')
    header_html = html[header_at:shell_at]
    assert 'Submission Forms' not in header_html
    assert 'href="/reports"' not in header_html
    assert 'href="/weekly"' not in header_html
    side_nav_at = html.index('class="side-nav"')
    assert side_nav_at < html.index('<main class="container">')
    assert html.find('data-tab="dashboard"', side_nav_at) > side_nav_at


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
