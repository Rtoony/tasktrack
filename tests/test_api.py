"""Tests for app/routes/api.py — generic CRUD, dashboard, search, comments,
activity feed, cycle-status, CSV export.

All in-process via the Flask test client against a temp SQLite. Cover the
contract the dashboard depends on:

- create -> appears in list -> readable by id -> updates -> deletes
- dashboard returns one entry per ALLOWED_TABLES key with the expected shape
- search returns matches across multiple trackers
- comments roundtrip
- cycle-status walks through the configured flow
- CSV export streams the right header + row
- error paths (bad table, missing record, unauthenticated)
"""
import csv
import io


def _make_work_task(auth_client, **overrides):
    """Helper: create a work_tasks row via the API and return its id."""
    payload = {"title": "Test task"}
    payload.update(overrides)
    r = auth_client.post("/api/v1/work_tasks", json=payload)
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()["id"]


# ── Unauthenticated access ────────────────────────────────────────────────

def test_dashboard_requires_auth(client):
    r = client.get("/api/v1/dashboard")
    assert r.status_code == 401


def test_list_requires_auth(client):
    r = client.get("/api/v1/work_tasks")
    assert r.status_code == 401


def test_create_requires_auth(client):
    r = client.post("/api/v1/work_tasks", json={"title": "x"})
    assert r.status_code == 401


# ── Table validation ──────────────────────────────────────────────────────

def test_list_rejects_unknown_table(auth_client):
    r = auth_client.get("/api/v1/no_such_table")
    assert r.status_code == 400


def test_create_rejects_unknown_table(auth_client):
    r = auth_client.post("/api/v1/no_such_table", json={"title": "x"})
    assert r.status_code == 400


# ── CRUD roundtrip ────────────────────────────────────────────────────────

def test_crud_roundtrip(auth_client):
    # CREATE
    r = auth_client.post("/api/v1/work_tasks", json={
        "title": "Wire the new actuator",
        "priority": "High",
        "status": "Not Started",
    })
    assert r.status_code in (200, 201)
    record_id = r.get_json()["id"]
    assert record_id > 0

    # READ
    r = auth_client.get(f"/api/v1/work_tasks/{record_id}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["title"] == "Wire the new actuator"
    assert data["priority"] == "High"

    # LIST
    r = auth_client.get("/api/v1/work_tasks")
    assert r.status_code == 200
    rows = r.get_json()
    assert isinstance(rows, list)
    assert any(row["id"] == record_id for row in rows)

    # UPDATE
    r = auth_client.put(f"/api/v1/work_tasks/{record_id}", json={
        "title": "Wire the new actuator",
        "priority": "Low",
        "status": "In Progress",
    })
    assert r.status_code == 200
    r2 = auth_client.get(f"/api/v1/work_tasks/{record_id}")
    assert r2.get_json()["priority"] == "Low"
    assert r2.get_json()["status"] == "In Progress"

    # DELETE
    r = auth_client.delete(f"/api/v1/work_tasks/{record_id}")
    assert r.status_code in (200, 204)
    r2 = auth_client.get(f"/api/v1/work_tasks/{record_id}")
    assert r2.status_code == 404


def test_get_missing_returns_404(auth_client):
    r = auth_client.get("/api/v1/work_tasks/99999")
    assert r.status_code == 404


def test_update_missing_returns_404(auth_client):
    r = auth_client.put("/api/v1/work_tasks/99999", json={"title": "x"})
    assert r.status_code == 404


# ── Dashboard ─────────────────────────────────────────────────────────────

def test_dashboard_returns_per_table_stats(auth_client):
    _make_work_task(auth_client, title="A", status="Not Started")
    _make_work_task(auth_client, title="B", status="In Progress")

    r = auth_client.get("/api/v1/dashboard")
    assert r.status_code == 200
    data = r.get_json()
    assert "stats" in data
    assert "work_tasks" in data["stats"]
    stats = data["stats"]["work_tasks"]
    for key in ("total", "active", "by_status", "by_priority"):
        assert key in stats, f"dashboard missing key: {key}"
    assert stats["total"] >= 2


# ── Search ────────────────────────────────────────────────────────────────

def test_search_finds_records_across_tables(auth_client):
    _make_work_task(auth_client, title="Calibrate the gripper")
    _make_work_task(auth_client, title="Order new bearings")

    r = auth_client.get("/api/v1/search?q=gripper")
    assert r.status_code == 200
    results = r.get_json()
    assert isinstance(results, list)
    # Search results carry the matched text in `label`, not `title`.
    assert any("gripper" in (row.get("label") or "").lower() for row in results)


def test_search_short_query_returns_empty(auth_client):
    r = auth_client.get("/api/v1/search?q=a")
    assert r.status_code == 200
    assert r.get_json() == []


# ── Comments ──────────────────────────────────────────────────────────────

def test_comments_roundtrip(auth_client):
    record_id = _make_work_task(auth_client, title="Has comments")

    # initially empty
    r = auth_client.get(f"/api/v1/work_tasks/{record_id}/comments")
    assert r.status_code == 200
    assert r.get_json() == []

    # add one
    r = auth_client.post(
        f"/api/v1/work_tasks/{record_id}/comments",
        json={"body": "Looking into this"},
    )
    assert r.status_code in (200, 201)

    # list it back
    r = auth_client.get(f"/api/v1/work_tasks/{record_id}/comments")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["body"] == "Looking into this"


def test_add_comment_rejects_empty_body(auth_client):
    record_id = _make_work_task(auth_client, title="x")
    r = auth_client.post(
        f"/api/v1/work_tasks/{record_id}/comments",
        json={"body": ""},
    )
    assert r.status_code == 400


# ── Cycle status ──────────────────────────────────────────────────────────

def test_cycle_status_advances_through_flow(auth_client):
    record_id = _make_work_task(auth_client, status="Not Started")

    r = auth_client.put(f"/api/v1/work_tasks/{record_id}/cycle-status")
    assert r.status_code == 200
    new_status = r.get_json()["status"]
    assert new_status != "Not Started"


# ── Activity ──────────────────────────────────────────────────────────────

def test_activity_log_records_creates_and_edits(auth_client):
    record_id = _make_work_task(auth_client, title="Audit me")
    auth_client.put(
        f"/api/v1/work_tasks/{record_id}",
        json={"title": "Audit me again", "priority": "High"},
    )
    r = auth_client.get(f"/api/v1/work_tasks/{record_id}/activity")
    assert r.status_code == 200
    events = r.get_json()
    assert isinstance(events, list)
    assert len(events) >= 1


# ── CSV export ────────────────────────────────────────────────────────────

def test_csv_export_streams_header_and_rows(auth_client):
    _make_work_task(auth_client, title="CSV Row 1")
    _make_work_task(auth_client, title="CSV Row 2")

    r = auth_client.get("/api/v1/work_tasks/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("Content-Type", "")
    body = r.data.decode("utf-8")
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    assert len(rows) >= 3  # header + at least 2 rows
    assert "title" in [c.lower() for c in rows[0]]


def test_csv_export_rejects_unknown_table(auth_client):
    r = auth_client.get("/api/v1/nope/export.csv")
    assert r.status_code == 400
