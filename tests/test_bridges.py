"""Cross-tracker bridge tests (Phase 3).

One test per supported (source, target) pair, plus the cross-cutting
concerns: rejected unknown pair, missing required override, idempotency
short-circuit, and dual activity_log writes.
"""
from sqlalchemy import select

from app.db import get_session
from app.models import ActivityLog, PersonnelIssue, TrainingTask, WorkTask

# ── Targets listing ──────────────────────────────────────────────────────


def test_targets_listing_for_personnel(auth_client):
    r = auth_client.get("/api/v1/bridge/personnel_issues/targets")
    assert r.status_code == 200
    targets = {row["target"] for row in r.get_json()}
    assert {"training_tasks", "work_tasks"}.issubset(targets)


def test_targets_listing_unknown_source_returns_empty(auth_client):
    r = auth_client.get("/api/v1/bridge/no_such_table/targets")
    assert r.status_code == 200
    assert r.get_json() == []


def test_targets_listing_requires_auth(client):
    r = client.get("/api/v1/bridge/personnel_issues/targets")
    assert r.status_code == 401


# ── Bridge: personnel_issues → training_tasks ────────────────────────────


def test_bridge_personnel_to_training(auth_client, temp_app):
    src = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Alice Drafter",
        "issue_description": "Struggling with sheet sets",
        "cad_skill_area": "Sheet Production",
        "recommended_training": "1:1 sheet-set workshop",
    }).get_json()
    src_id = src["id"]

    r = auth_client.post(
        f"/api/v1/bridge/personnel_issues/{src_id}/training_tasks",
    )
    assert r.status_code == 201
    body = r.get_json()
    assert body["target_table"] == "training_tasks"
    tgt_id = body["target_id"]
    assert tgt_id > 0

    with temp_app.app_context():
        sess = get_session()
        tgt = sess.get(TrainingTask, tgt_id)
        assert tgt is not None
        # Field map carried correctly.
        assert tgt.trainees == "Alice Drafter"
        assert tgt.skill_area == "Sheet Production"
        assert tgt.training_goals == "1:1 sheet-set workshop"
        assert tgt.additional_context == "Struggling with sheet sets"
        # Title template applied.
        assert "Alice Drafter" in tgt.title


def test_bridge_writes_dual_activity_log(auth_client, temp_app):
    src = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Bob",
        "issue_description": "needs help",
    }).get_json()

    auth_client.post(
        f"/api/v1/bridge/personnel_issues/{src['id']}/training_tasks",
    )

    with temp_app.app_context():
        sess = get_session()
        # Source side: bridged_to:<target_table> marker.
        src_logs = sess.scalars(
            select(ActivityLog).where(
                ActivityLog.table_name == "personnel_issues",
                ActivityLog.record_id == src["id"],
            )
        ).all()
        actions = [r.action for r in src_logs]
        assert any(a.startswith("bridged_to:training_tasks") for a in actions)

        # Target side: bridged_from marker.
        tgt_logs = sess.scalars(
            select(ActivityLog).where(
                ActivityLog.table_name == "training_tasks",
                ActivityLog.action == "bridged_from",
            )
        ).all()
        assert len(tgt_logs) >= 1


# ── Bridge: personnel_issues → work_tasks ────────────────────────────────


def test_bridge_personnel_to_work(auth_client, temp_app):
    src = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Eve",
        "issue_description": "Doesn't know our layer std",
        "cad_skill_area": "CAD Standards",
        "project_number": "1234.56",
    }).get_json()

    r = auth_client.post(
        f"/api/v1/bridge/personnel_issues/{src['id']}/work_tasks",
    )
    assert r.status_code == 201
    tgt_id = r.get_json()["target_id"]

    with temp_app.app_context():
        sess = get_session()
        tgt = sess.get(WorkTask, tgt_id)
        assert tgt.description == "Doesn't know our layer std"
        assert tgt.cad_skill_area == "CAD Standards"
        assert tgt.project_number == "1234.56"
        assert "Eve" in tgt.title


# ── Bridge: work_tasks → personnel_issues (override required) ────────────


def test_bridge_work_to_personnel_requires_override(auth_client):
    src = auth_client.post("/api/v1/work_tasks", json={
        "title": "Big rendering bug",
        "description": "Renderer barfs on long sheet sets",
    }).get_json()

    # No overrides → 400, telling us the required override.
    r = auth_client.post(
        f"/api/v1/bridge/work_tasks/{src['id']}/personnel_issues",
    )
    assert r.status_code == 400
    assert "person_name" in r.get_json()["error"]


def test_bridge_work_to_personnel_with_override(auth_client, temp_app):
    src = auth_client.post("/api/v1/work_tasks", json={
        "title": "Big rendering bug",
        "description": "Renderer barfs on long sheet sets",
        "cad_skill_area": "Sheet Production",
    }).get_json()

    r = auth_client.post(
        f"/api/v1/bridge/work_tasks/{src['id']}/personnel_issues",
        json={"overrides": {"person_name": "Charlie"}},
    )
    assert r.status_code == 201
    tgt_id = r.get_json()["target_id"]

    with temp_app.app_context():
        sess = get_session()
        tgt = sess.get(PersonnelIssue, tgt_id)
        assert tgt.person_name == "Charlie"
        assert tgt.issue_description == "Renderer barfs on long sheet sets"
        assert tgt.cad_skill_area == "Sheet Production"


def test_bridge_training_to_personnel(auth_client, temp_app):
    src = auth_client.post("/api/v1/training_tasks", json={
        "title": "Civil 3D fundamentals",
        "trainees": "Daniel",
        "skill_area": "Civil Design",
        "training_goals": "Get up to speed",
    }).get_json()

    r = auth_client.post(
        f"/api/v1/bridge/training_tasks/{src['id']}/personnel_issues",
        json={"overrides": {"issue_description": "Still struggling post-training"}},
    )
    assert r.status_code == 201

    tgt_id = r.get_json()["target_id"]
    with temp_app.app_context():
        sess = get_session()
        tgt = sess.get(PersonnelIssue, tgt_id)
        assert tgt.person_name == "Daniel"
        assert tgt.cad_skill_area == "Civil Design"
        assert tgt.recommended_training == "Get up to speed"


# ── Idempotency ───────────────────────────────────────────────────────────


def test_bridge_idempotent_with_key(auth_client):
    src = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Idem Person",
        "issue_description": "x",
    }).get_json()

    first = auth_client.post(
        f"/api/v1/bridge/personnel_issues/{src['id']}/training_tasks",
        json={"idempotency_key": "key-abc"},
    ).get_json()
    second = auth_client.post(
        f"/api/v1/bridge/personnel_issues/{src['id']}/training_tasks",
        json={"idempotency_key": "key-abc"},
    ).get_json()
    assert first["target_id"] == second["target_id"]


def test_bridge_no_key_creates_separate_rows(auth_client):
    src = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Dup Person",
        "issue_description": "x",
    }).get_json()

    a = auth_client.post(
        f"/api/v1/bridge/personnel_issues/{src['id']}/training_tasks",
    ).get_json()
    b = auth_client.post(
        f"/api/v1/bridge/personnel_issues/{src['id']}/training_tasks",
    ).get_json()
    assert a["target_id"] != b["target_id"]


# ── Error paths ──────────────────────────────────────────────────────────


def test_bridge_unknown_source_table(auth_client):
    r = auth_client.post("/api/v1/bridge/no_such/1/training_tasks")
    assert r.status_code == 400


def test_bridge_unknown_target_table(auth_client):
    src = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "X", "issue_description": "y",
    }).get_json()
    r = auth_client.post(
        f"/api/v1/bridge/personnel_issues/{src['id']}/no_such_target",
    )
    assert r.status_code == 400


def test_bridge_disallowed_pair(auth_client):
    """personal_items → work_tasks is not in BRIDGE_MAP — must reject."""
    src = auth_client.post("/api/v1/personal_items", json={
        "title": "buy milk",
        "category": "House",
    }).get_json()
    r = auth_client.post(
        f"/api/v1/bridge/personal_items/{src['id']}/work_tasks",
    )
    assert r.status_code == 400


def test_bridge_missing_source_id(auth_client):
    r = auth_client.post("/api/v1/bridge/personnel_issues/99999/training_tasks")
    assert r.status_code == 404


def test_bridge_requires_auth(client):
    r = client.post("/api/v1/bridge/personnel_issues/1/training_tasks")
    assert r.status_code == 401
