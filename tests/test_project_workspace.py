"""Sprint 3 project workspace read surface tests."""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import (
    CalendarEvent,
    PersonnelIssue,
    Project,
    ProjectSite,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
)


def _seed_workspace(sess):
    proj = Project(
        project_number="8800.10",
        name="Workspace project",
        client="Acme Water",
        component="Topographic Mapping",
        external_system="nexus-projects",
        external_ref="np-8800-10",
        lat=38.1,
        lng=-122.1,
    )
    sess.add(proj)
    sess.flush()
    sess.add(ProjectSite(project_id=proj.id, lat=38.1, lng=-122.1, pin_color="yellow", is_primary=1))
    sess.add(ProjectSite(project_id=proj.id, lat=38.2, lng=-122.2, pin_color="green", is_primary=0))
    sess.add(WorkTask(title="CAD setup", project_id=proj.id, project_number="8800.10"))
    sess.add(ProjectWorkTask(
        project_name="Workspace project",
        title="Draft exhibit",
        project_number="8800.10",
        task_description="Prepare exhibit",
        project_id=proj.id,
    ))
    sess.add(TrainingTask(title="Brief team", project_number="8800.10", project_id=proj.id))
    sess.add(PersonnelIssue(
        person_name="Sensitive Employee",
        issue_description="Process issue",
        incident_context="Private context",
        recommended_training="Private training",
        project_number="8800.10",
        project_id=proj.id,
    ))
    sess.add(CalendarEvent(
        title="Project review",
        event_type="review",
        start_at=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
        project_number="8800.10",
        project_id=proj.id,
    ))
    sess.add(Project(project_number="8800.20", name="Other", lat=39.1, lng=-121.1))
    sess.commit()
    return proj.id


def test_project_workspace_by_id_and_number(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj_id = _seed_workspace(sess)

    for url in (f"/api/v1/projects/{proj_id}/workspace", "/api/v1/projects/workspace?project_number=8800.10"):
        r = auth_client.get(url)
        assert r.status_code == 200
        body = r.get_json()
        assert body["project"]["project_number"] == "8800.10"
        assert body["external"] == {"system": "nexus-projects", "ref": "np-8800-10"}
        assert body["counts"]["sites"] == 2
        assert body["counts"]["work_tasks"] == 1
        assert body["counts"]["project_work_tasks"] == 1
        assert body["counts"]["training_tasks"] == 1
        assert body["counts"]["personnel_issues"] == 1
        assert body["counts"]["calendar_events"] == 1
        assert body["linked_records"]["project_work_tasks"][0]["title"] == "Draft exhibit"
        capability = body["linked_records"]["personnel_issues"][0]
        assert capability["title"] == "Capability note (restricted)"
        assert capability["redacted"] is True
        assert "issue_description" not in capability
        assert "person_name" not in capability


def test_project_workspace_shows_capability_narratives_to_admin(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj_id = _seed_workspace(sess)

    r = admin_client.get(f"/api/v1/projects/{proj_id}/workspace")
    assert r.status_code == 200
    body = r.get_json()
    capability = body["linked_records"]["personnel_issues"][0]
    assert capability["issue_description"] == "Process issue"
    assert capability["person_name"] == "Sensitive Employee"


def test_project_workspace_hides_private_calendar_events_from_other_users(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj = Project(project_number="8800.50", name="Privacy project")
        sess.add(proj)
        sess.flush()
        sess.add(CalendarEvent(
            title="Shared project review",
            event_type="review",
            start_at=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
            project_number="8800.50",
            project_id=proj.id,
            visibility="internal",
            created_by_user_id=1,
        ))
        sess.add(CalendarEvent(
            title="Private project prep",
            event_type="prep",
            start_at=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
            project_number="8800.50",
            project_id=proj.id,
            visibility="private",
            created_by_user_id=1,
        ))
        sess.commit()
        proj_id = proj.id

    with auth_client.session_transaction() as s:
        s["user_id"] = 2
        s["user_name"] = "Other User"
        s["user_role"] = "user"

    r = auth_client.get(f"/api/v1/projects/{proj_id}/workspace")
    assert r.status_code == 200
    body = r.get_json()
    titles = {row["title"] for row in body["linked_records"]["calendar_events"]}
    assert "Shared project review" in titles
    assert "Private project prep" not in titles
    assert body["counts"]["calendar_events"] == 1

def test_project_workspace_requires_auth(client):
    assert client.get("/api/v1/projects/1/workspace").status_code == 401


def test_project_workspace_errors(auth_client):
    assert auth_client.get("/api/v1/projects/workspace").status_code == 400
    assert auth_client.get("/api/v1/projects/workspace?project_number=missing").status_code == 404
    assert auth_client.get("/api/v1/projects/999999/workspace").status_code == 404


def test_geojson_exact_project_filters(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj_id = _seed_workspace(sess)

    by_number = auth_client.get("/api/v1/projects/geojson?project_number=8800.10").get_json()
    nums = {f["properties"]["project_number"] for f in by_number["features"]}
    assert nums == {"8800.10"}
    assert len(by_number["features"]) == 2

    by_id = auth_client.get(f"/api/v1/projects/geojson?project_id={proj_id}").get_json()
    ids = {f["properties"]["project_id"] for f in by_id["features"]}
    assert ids == {proj_id}

    bad = auth_client.get("/api/v1/projects/geojson?project_id=nope")
    assert bad.status_code == 400


def test_hulls_exact_project_filters(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        proj = Project(project_number="8800.30", name="Hull project", component="Topographic Mapping")
        sess.add(proj)
        sess.flush()
        for lat, lng in ((38.0, -122.0), (38.1, -122.1), (38.2, -122.0)):
            sess.add(ProjectSite(project_id=proj.id, lat=lat, lng=lng, pin_color="yellow"))
        sess.add(Project(project_number="8800.40", name="Other hull"))
        sess.commit()
        proj_id = proj.id

    r = auth_client.get("/api/v1/projects/hulls?project_number=8800.30")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["features"]) == 1
    assert body["features"][0]["properties"]["project_number"] == "8800.30"

    bad = auth_client.get("/api/v1/projects/hulls?project_id=nope")
    assert bad.status_code == 400

    by_id = auth_client.get(f"/api/v1/projects/hulls?project_id={proj_id}").get_json()
    assert by_id["features"][0]["properties"]["project_id"] == proj_id

def test_dashboard_exposes_project_workspace_ui(auth_client):
    r = auth_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="workspace-overlay"' in html
    assert 'openProjectWorkspaceById' in html
    assert 'openProjectWorkspaceSmart' in html
    assert 'focusProjectOnMapSmart' in html
    assert 'openMeetingPacketForEvent' in html
    assert 'startupRouteState' in html
    assert "params.get('workspace')" in html
    assert "params.get('map_project')" in html
    assert 'Capability narratives are restricted here' in html
    assert "section.id = 'workspace-section-'" in html
    assert 'id="map-project-number-filter"' in html
    assert "type:'project-picker'" in html
    assert 'normalizeProjectNumberCandidate' in html
    assert 'Project (registry)' not in html
    assert 'Project Number must use' not in html
