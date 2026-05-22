"""Tests for Phase-0.5 (Atlas-lite) — lat/lng/display_status on projects
plus the read-open list endpoint and the GeoJSON FeatureCollection feed.

Also covers the master-list-import additions (component / principal /
start_date / dormant_date, the {active, dormant} display_status enum,
the multi-site geojson feature shape, and the components endpoint).
"""
from app.db import get_session
from app.models import Project, ProjectSite


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
        "lat": 40.0, "lng": -120.0, "display_status": "dormant",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["lat"] == 40.0
    assert body["lng"] == -120.0
    assert body["display_status"] == "dormant"


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


# ── Master-list-import surface ────────────────────────────────────────────


def test_create_project_with_master_list_fields(admin_client):
    r = admin_client.post("/api/v1/projects", json={
        "project_number": "9300.00",
        "name": "Test water tank",
        "client": "City of Springfield",
        "component": "Water Storage Tank",
        "principal": "Long, David",
        "start_date": "2025-03-01",
        "dormant_date": "",
        "display_status": "active",
    })
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["component"] == "Water Storage Tank"
    assert body["principal"] == "Long, David"
    assert body["start_date"] == "2025-03-01"
    assert body["dormant_date"] == ""


def test_patch_master_list_fields(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="9301.00", name="Initial"))
        sess.commit()
    rows = admin_client.get("/api/v1/projects?include_inactive=1").get_json()
    proj_id = next(r["id"] for r in rows if r["project_number"] == "9301.00")
    r = admin_client.patch(f"/api/v1/projects/{proj_id}", json={
        "component": "Topographic Mapping",
        "dormant_date": "2026-01-15",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["component"] == "Topographic Mapping"
    assert body["dormant_date"] == "2026-01-15"


def test_create_project_rejects_old_status_values(admin_client):
    """The {active, dormant} enum is now strict — completed/review/draft
    were never used in practice and got pared down."""
    for bad in ("completed", "review", "draft"):
        r = admin_client.post("/api/v1/projects", json={
            "project_number": f"9310.{bad[:2]}",
            "display_status": bad,
        })
        assert r.status_code == 400, f"{bad!r} should be rejected"


def test_geojson_multi_site_emits_one_feature_per_pin(auth_client, temp_app):
    """A project with two ProjectSite rows must show two pins on the map.
    The master-list import puts ~360 such projects in the live DB."""
    with temp_app.app_context():
        sess = get_session()
        proj = Project(
            project_number="9400.00", name="Two-site project",
            lat=38.0, lng=-122.0,
            component="Site Improvement Plans",
        )
        sess.add(proj)
        sess.flush()
        sess.add(ProjectSite(project_id=proj.id, lat=38.10, lng=-122.10,
                             pin_color="yellow", is_primary=1, raw_name="9400"))
        sess.add(ProjectSite(project_id=proj.id, lat=38.20, lng=-122.20,
                             pin_color="red", is_primary=0, raw_name="9400"))
        sess.commit()
    body = auth_client.get("/api/v1/projects/geojson").get_json()
    sites = [f for f in body["features"]
             if f["properties"]["project_number"] == "9400.00"]
    assert len(sites) == 2
    colors = {f["properties"]["pin_color"] for f in sites}
    assert colors == {"yellow", "red"}
    # Properties carry the new fields:
    assert all(f["properties"]["component"] == "Site Improvement Plans"
               for f in sites)


def test_geojson_component_filter(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="9500.00", lat=38.0, lng=-122.0,
                         component="Topographic Mapping"))
        sess.add(Project(project_number="9501.00", lat=38.5, lng=-122.5,
                         component="Site Improvement Plans"))
        sess.commit()
    r = auth_client.get(
        "/api/v1/projects/geojson?component=Topographic%20Mapping"
    )
    nums = {f["properties"]["project_number"] for f in r.get_json()["features"]}
    assert "9500.00" in nums
    assert "9501.00" not in nums


def test_geojson_pin_color_filter(auth_client, temp_app):
    """pin_color filter narrows the FeatureCollection to a single
    artifact category — without it both sites should show."""
    with temp_app.app_context():
        sess = get_session()
        proj = Project(project_number="9600.00", lat=38.0, lng=-122.0)
        sess.add(proj)
        sess.flush()
        sess.add(ProjectSite(project_id=proj.id, lat=38.0, lng=-122.0,
                             pin_color="yellow", is_primary=1))
        sess.add(ProjectSite(project_id=proj.id, lat=38.5, lng=-122.5,
                             pin_color="green", is_primary=0))
        sess.commit()
    r = auth_client.get("/api/v1/projects/geojson?pin_color=green")
    feats = r.get_json()["features"]
    matching = [f for f in feats if f["properties"]["project_number"] == "9600.00"]
    assert len(matching) == 1
    assert matching[0]["properties"]["pin_color"] == "green"


def test_components_endpoint(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(Project(project_number="9700.00", component="Topographic Mapping"))
        sess.add(Project(project_number="9701.00", component="Topographic Mapping"))
        sess.add(Project(project_number="9702.00", component="Pump Station"))
        sess.add(Project(project_number="9703.00", component=""))  # blank — excluded
        sess.commit()
    r = auth_client.get("/api/v1/projects/components")
    assert r.status_code == 200
    rows = r.get_json()
    by_comp = {row["component"]: row["count"] for row in rows}
    assert by_comp["Topographic Mapping"] == 2
    assert by_comp["Pump Station"] == 1
    assert "" not in by_comp
