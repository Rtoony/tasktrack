"""Tests for the multi-site convex hull endpoint + helper.

Covers:
- The pure helper in app.services.convex_hull
- /api/v1/projects/hulls — only projects with 3+ sites get a polygon
- Filters (display_status, component, client, pin_color) are honored
"""
from app.db import get_session
from app.models import Project, ProjectSite
from app.services.convex_hull import convex_hull, hull_geojson_ring


# ───────── pure helper ─────────

def test_convex_hull_empty():
    assert convex_hull([]) == []


def test_convex_hull_single_point():
    assert convex_hull([(1.0, 2.0)]) == [(1.0, 2.0)]


def test_convex_hull_two_points_returns_segment():
    assert convex_hull([(0.0, 0.0), (1.0, 1.0)]) == [(0.0, 0.0), (1.0, 1.0)]


def test_convex_hull_triangle_is_itself():
    pts = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    hull = convex_hull(pts)
    assert len(hull) == 3
    assert set(hull) == set(pts)


def test_convex_hull_square_drops_interior_point():
    # Five points: four corners of a square + one interior. Hull is
    # just the four corners.
    pts = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (1.0, 1.0)]
    hull = convex_hull(pts)
    assert len(hull) == 4
    assert (1.0, 1.0) not in hull


def test_convex_hull_duplicates_collapsed():
    # 4 distinct corners + duplicates → still 4 corners
    pts = [(0.0, 0.0), (0.0, 0.0), (2.0, 0.0), (2.0, 0.0),
           (2.0, 2.0), (0.0, 2.0)]
    hull = convex_hull(pts)
    assert len(hull) == 4


def test_hull_geojson_ring_closes_polygon():
    pts = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    ring = hull_geojson_ring(pts)
    assert ring is not None
    assert ring[0] == ring[-1]
    # Each entry is [lng, lat] order (note: helper swaps to GeoJSON order)
    assert all(len(p) == 2 for p in ring)


def test_hull_geojson_ring_skips_degenerate():
    assert hull_geojson_ring([]) is None
    assert hull_geojson_ring([(0.0, 0.0)]) is None
    assert hull_geojson_ring([(0.0, 0.0), (1.0, 1.0)]) is None


# ───────── endpoint ─────────

def _seed_project_with_sites(sess, *, project_number, sites, **proj_kwargs):
    """Helper: insert a project + N sites."""
    proj = Project(project_number=project_number, **proj_kwargs)
    sess.add(proj); sess.flush()
    for lat, lng, pin_color in sites:
        sess.add(ProjectSite(
            project_id=proj.id, lat=lat, lng=lng,
            pin_color=pin_color, is_primary=False,
        ))
    sess.commit()
    return proj


def test_hulls_endpoint_returns_polygon_for_three_site_project(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_project_with_sites(
            sess, project_number="HULL-3", display_status="active",
            sites=[(38.0, -122.0, "yellow"),
                   (38.1, -122.0, "yellow"),
                   (38.05, -121.9, "yellow")],
        )
    r = admin_client.get("/api/v1/projects/hulls")
    assert r.status_code == 200
    body = r.get_json()
    assert body["type"] == "FeatureCollection"
    feats = [f for f in body["features"]
             if f["properties"]["project_number"] == "HULL-3"]
    assert len(feats) == 1
    poly = feats[0]
    assert poly["geometry"]["type"] == "Polygon"
    ring = poly["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert poly["properties"]["site_count"] == 3


def test_hulls_endpoint_skips_two_site_project(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_project_with_sites(
            sess, project_number="HULL-2", display_status="active",
            sites=[(38.0, -122.0, "yellow"),
                   (38.1, -122.0, "yellow")],
        )
    r = admin_client.get("/api/v1/projects/hulls")
    body = r.get_json()
    assert not any(f["properties"]["project_number"] == "HULL-2"
                   for f in body["features"])


def test_hulls_endpoint_skips_single_site_project(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_project_with_sites(
            sess, project_number="HULL-1", display_status="active",
            sites=[(38.0, -122.0, "yellow")],
        )
    r = admin_client.get("/api/v1/projects/hulls")
    body = r.get_json()
    assert not any(f["properties"]["project_number"] == "HULL-1"
                   for f in body["features"])


def test_hulls_endpoint_filters_by_display_status(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_project_with_sites(
            sess, project_number="HULL-ACT", display_status="active",
            sites=[(38.0, -122.0, "yellow"), (38.1, -122.0, "yellow"),
                   (38.05, -121.9, "yellow")],
        )
        _seed_project_with_sites(
            sess, project_number="HULL-DOR", display_status="dormant",
            sites=[(39.0, -123.0, "red"), (39.1, -123.0, "red"),
                   (39.05, -122.9, "red")],
        )
    r = admin_client.get("/api/v1/projects/hulls?display_status=active")
    nums = {f["properties"]["project_number"] for f in r.get_json()["features"]}
    assert "HULL-ACT" in nums
    assert "HULL-DOR" not in nums


def test_hulls_endpoint_filters_by_pin_color_can_collapse_to_degenerate(admin_client, temp_app):
    # A project with 3 sites of mixed pin colors. Filtering by one
    # color leaves <3 sites, so the hull is skipped.
    with temp_app.app_context():
        sess = get_session()
        _seed_project_with_sites(
            sess, project_number="HULL-MIXED", display_status="active",
            sites=[(38.0, -122.0, "yellow"),
                   (38.1, -122.0, "red"),
                   (38.05, -121.9, "red")],
        )
    r = admin_client.get("/api/v1/projects/hulls?pin_color=yellow")
    nums = {f["properties"]["project_number"] for f in r.get_json()["features"]}
    assert "HULL-MIXED" not in nums  # only 1 yellow site, hull skipped
    r2 = admin_client.get("/api/v1/projects/hulls?pin_color=red")
    nums2 = {f["properties"]["project_number"] for f in r2.get_json()["features"]}
    assert "HULL-MIXED" not in nums2  # only 2 red sites, still skipped


def test_hulls_endpoint_requires_login(client):
    r = client.get("/api/v1/projects/hulls")
    assert r.status_code == 401
