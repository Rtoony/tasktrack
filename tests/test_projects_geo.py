"""Tests for Phase-0.5 (Atlas-lite) — lat/lng/display_status on projects
plus the read-open list endpoint and the GeoJSON FeatureCollection feed.
"""
from app.db import get_session
from app.models import Project


def test_create_project_with_geo(admin_client):
    r = admin_client.post("/api/v1/projects", json={
        "project_number": "9001.00",
        "name": "Sample bridge",
        "client": "Test client",
        "lat": 38.4404,
        "lng": -122.7141,
        "display_status": "active",
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["lat"] == 38.4404
    assert body["lng"] == -122.7141
    assert body["display_status"] == "active"


def test_create_project_rejects_bad_status(admin_client):
    r = admin_client.post("/api/v1/projects", json={
        "project_number": "9002.00",
        "display_status": "not-a-real-status",
    })
    assert r.status_code == 400


def test_patch_geo_fields(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="9003.00"))
        sess.commit()
    rows = admin_client.get("/api/v1/projects?include_inactive=1").get_json()
    proj_id = next(r["id"] for r in rows if r["project_number"] == "9003.00")

    r = admin_client.patch(f"/api/v1/projects/{proj_id}", json={
        "lat": 40.0, "lng": -120.0, "display_status": "review",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["lat"] == 40.0
    assert body["lng"] == -120.0
    assert body["display_status"] == "review"


def test_patch_rejects_bad_status(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="9004.00"))
        sess.commit()
    rows = admin_client.get("/api/v1/projects?include_inactive=1").get_json()
    proj_id = next(r["id"] for r in rows if r["project_number"] == "9004.00")

    r = admin_client.patch(f"/api/v1/projects/{proj_id}", json={
        "display_status": "weird",
    })
    assert r.status_code == 400


# ── List endpoint is now login_required (was admin_required) ──────────────


def test_list_projects_open_to_regular_user(auth_client, temp_app):
    """Phase-0.5 opened the read endpoint so the fk-select and Map tab
    work for non-admins."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="9100.00", name="Open"))
        sess.commit()
    r = auth_client.get("/api/v1/projects")
    assert r.status_code == 200
    numbers = {row["project_number"] for row in r.get_json()}
    assert "9100.00" in numbers


def test_list_projects_still_blocks_anonymous(client):
    r = client.get("/api/v1/projects")
    assert r.status_code == 401


def test_mutations_still_admin_only(auth_client):
    """POST/PATCH/DELETE remain admin-only after the GET was relaxed."""
    r = auth_client.post("/api/v1/projects", json={"project_number": "9200.00"})
    assert r.status_code == 403


# ── GeoJSON feed ──────────────────────────────────────────────────────────


def test_geojson_skips_projects_without_latlng(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="G001.00", lat=38.0, lng=-122.0))
        sess.add(Project(project_number="G002.00"))  # no lat/lng
        sess.commit()
    r = auth_client.get("/api/v1/projects/geojson")
    assert r.status_code == 200
    body = r.get_json()
    assert body["type"] == "FeatureCollection"
    numbers = {f["properties"]["project_number"] for f in body["features"]}
    assert "G001.00" in numbers
    assert "G002.00" not in numbers


def test_geojson_coordinate_order_is_lng_lat(auth_client, temp_app):
    """GeoJSON spec is [longitude, latitude] — a common bug."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="G003.00", lat=38.5, lng=-122.5))
        sess.commit()
    r = auth_client.get("/api/v1/projects/geojson")
    feat = next(f for f in r.get_json()["features"]
                if f["properties"]["project_number"] == "G003.00")
    lng, lat = feat["geometry"]["coordinates"]
    assert lng == -122.5
    assert lat == 38.5


def test_geojson_bbox_filter(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="G010.00", lat=38.0, lng=-122.0))  # inside
        sess.add(Project(project_number="G011.00", lat=45.0, lng=-110.0))  # outside
        sess.commit()
    # Bounding box around Sonoma County
    r = auth_client.get("/api/v1/projects/geojson?bbox=-123,37,-121,39")
    numbers = {f["properties"]["project_number"] for f in r.get_json()["features"]}
    assert "G010.00" in numbers
    assert "G011.00" not in numbers


def test_geojson_requires_auth(client):
    r = client.get("/api/v1/projects/geojson")
    assert r.status_code == 401
