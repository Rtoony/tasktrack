"""Saved Views (WORK_PLAN P1-5) tests.

Covers the CRUD lifecycle of per-user tracker filter/sort configurations
and the apply-filters round-trip (save a state blob, list it back, confirm
it is byte-faithful so the frontend can re-apply it). Also locks in the
per-user isolation, name-overwrite, and validation contracts.
"""


def _sample_state(status="__active", sort_col="due_at", sort_order="asc"):
    """A representative captured filter-bar state for the work tab."""
    return {
        "filters": {
            "filter-work-status": status,
            "filter-work-priority": "High",
            "filter-work-archived": False,
        },
        "sort": {"col": sort_col, "order": sort_order},
    }


# ── Create / read ────────────────────────────────────────────────────────

def test_create_and_list_saved_view(auth_client):
    state = _sample_state()
    r = auth_client.post("/api/v1/saved-views", json={
        "name": "My overdue", "tab": "work", "state": state,
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["name"] == "My overdue"
    assert body["tab"] == "work"
    assert body["id"] > 0

    listed = auth_client.get("/api/v1/saved-views?tab=work").get_json()
    assert len(listed["views"]) == 1
    assert listed["views"][0]["name"] == "My overdue"


def test_apply_filters_state_roundtrips_faithfully(auth_client):
    """The whole point of P1-5: the saved state must come back exactly as
    stored so the frontend can re-apply filters + sort verbatim."""
    state = _sample_state(status="In Progress", sort_col="priority",
                          sort_order="desc")
    auth_client.post("/api/v1/saved-views", json={
        "name": "Hot work", "tab": "work", "state": state,
    })
    view = auth_client.get("/api/v1/saved-views?tab=work").get_json()["views"][0]
    assert view["state"] == state
    assert view["state"]["filters"]["filter-work-status"] == "In Progress"
    assert view["state"]["sort"] == {"col": "priority", "order": "desc"}


def test_list_is_filtered_by_tab(auth_client):
    auth_client.post("/api/v1/saved-views", json={
        "name": "W", "tab": "work", "state": _sample_state()})
    auth_client.post("/api/v1/saved-views", json={
        "name": "T", "tab": "triage", "state": {"filters": {}, "sort": {}}})

    work = auth_client.get("/api/v1/saved-views?tab=work").get_json()["views"]
    triage = auth_client.get("/api/v1/saved-views?tab=triage").get_json()["views"]
    assert [v["name"] for v in work] == ["W"]
    assert [v["name"] for v in triage] == ["T"]

    all_views = auth_client.get("/api/v1/saved-views").get_json()["views"]
    assert len(all_views) == 2


# ── Update via overwrite ─────────────────────────────────────────────────

def test_resaving_same_name_overwrites(auth_client):
    auth_client.post("/api/v1/saved-views", json={
        "name": "My overdue", "tab": "work",
        "state": _sample_state(status="__active")})
    r = auth_client.post("/api/v1/saved-views", json={
        "name": "My overdue", "tab": "work",
        "state": _sample_state(status="Complete")})
    assert r.status_code == 200  # overwrite, not a new row

    views = auth_client.get("/api/v1/saved-views?tab=work").get_json()["views"]
    assert len(views) == 1
    assert views[0]["state"]["filters"]["filter-work-status"] == "Complete"


def test_same_name_different_tab_is_distinct(auth_client):
    auth_client.post("/api/v1/saved-views", json={
        "name": "Mine", "tab": "work", "state": _sample_state()})
    auth_client.post("/api/v1/saved-views", json={
        "name": "Mine", "tab": "triage", "state": {"filters": {}, "sort": {}}})
    assert len(auth_client.get("/api/v1/saved-views").get_json()["views"]) == 2


# ── Delete ───────────────────────────────────────────────────────────────

def test_delete_saved_view(auth_client):
    vid = auth_client.post("/api/v1/saved-views", json={
        "name": "Disposable", "tab": "work",
        "state": _sample_state()}).get_json()["id"]
    r = auth_client.delete(f"/api/v1/saved-views/{vid}")
    assert r.status_code == 200
    assert auth_client.get("/api/v1/saved-views").get_json()["views"] == []


def test_delete_missing_view_404(auth_client):
    assert auth_client.delete("/api/v1/saved-views/99999").status_code == 404


# ── Per-user isolation ───────────────────────────────────────────────────

def test_views_are_per_user(client, temp_app):
    from tests.conftest import _seed_user

    # User 1 saves a view.
    _seed_user(temp_app, role="user", user_id=1, name="User One")
    with client.session_transaction() as s:
        s["user_id"], s["user_name"], s["user_role"] = 1, "User One", "user"
    vid = client.post("/api/v1/saved-views", json={
        "name": "Private", "tab": "work",
        "state": _sample_state()}).get_json()["id"]

    # User 2 must not see or delete it.
    _seed_user(temp_app, role="user", user_id=2, name="User Two")
    with client.session_transaction() as s:
        s["user_id"], s["user_name"], s["user_role"] = 2, "User Two", "user"
    assert client.get("/api/v1/saved-views").get_json()["views"] == []
    assert client.delete(f"/api/v1/saved-views/{vid}").status_code == 404


# ── Validation ───────────────────────────────────────────────────────────

def test_create_requires_name(auth_client):
    r = auth_client.post("/api/v1/saved-views", json={
        "tab": "work", "state": _sample_state()})
    assert r.status_code == 400


def test_create_rejects_unknown_tab(auth_client):
    r = auth_client.post("/api/v1/saved-views", json={
        "name": "X", "tab": "dashboard", "state": _sample_state()})
    assert r.status_code == 400


def test_create_rejects_non_object_state(auth_client):
    r = auth_client.post("/api/v1/saved-views", json={
        "name": "X", "tab": "work", "state": "not-an-object"})
    assert r.status_code == 400


def test_list_rejects_unknown_tab_filter(auth_client):
    assert auth_client.get("/api/v1/saved-views?tab=nope").status_code == 400


def test_endpoints_require_login(client):
    assert client.get("/api/v1/saved-views").status_code in (302, 401)
    assert client.post("/api/v1/saved-views", json={
        "name": "X", "tab": "work", "state": {}}).status_code in (302, 401)


# ── Frontend wiring ──────────────────────────────────────────────────────

def test_dashboard_ships_saved_views_module(auth_client):
    """The index template must carry the Saved Views JS so each tracker
    filter-bar gets the Save/load controls injected at init."""
    html = auth_client.get("/").data.decode("utf-8")
    assert "initSavedViews" in html
    assert "captureViewState" in html
    assert "applyViewState" in html
    assert "/api/v1/saved-views" in html
    assert "saved-views" in html  # the injected control class
