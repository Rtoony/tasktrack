"""Competency → Training bridge tests (WORK_PLAN P2-6).

Covers the virtual-source bridge that turns a competency gap (a low
(employee, category) cell) into a Training tracker task and reflects the
linkage both ways via activity_log markers. Mirrors the admin-only gating
and seeding style of test_competency.py / test_bridges.py.
"""
from sqlalchemy import and_, select

from app.db import get_session
from app.models import (
    ActivityLog,
    Employee,
    SkillCategory,
    TrainingTask,
)
from app.services.competency import upsert_score


def _seed_pair(temp_app, *, name="Gap Subject", cat_name="Civil 3D"):
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name=name, role="engineer")
        cat = SkillCategory(slug=cat_name.lower().replace(" ", "-"), name=cat_name)
        sess.add(emp)
        sess.add(cat)
        sess.commit()
        return emp.id, cat.id


def _set_score(temp_app, emp_id, cat_id, score):
    """Write a manual category-level score so the cell has a rollup.

    Uses a request context so the competency service's flask_session reads
    (created_by_user_id, activity-log actor) resolve — same as a real call."""
    with temp_app.test_request_context():
        sess = get_session()
        upsert_score(sess, emp_id, cat_id, score)
        sess.commit()


# ── Auth gating ────────────────────────────────────────────────────────────


def test_bridge_requires_admin(auth_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = auth_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training")
    assert r.status_code == 403


def test_link_list_requires_admin(auth_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = auth_client.get(f"/api/v1/skills/{emp_id}/{cat_id}/training")
    assert r.status_code == 403


def test_bridge_anonymous_blocked(client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    assert client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training").status_code == 401


# ── Validation ─────────────────────────────────────────────────────────────


def test_unknown_employee_404(admin_client, temp_app):
    _, cat_id = _seed_pair(temp_app)
    r = admin_client.post(f"/api/v1/skills/99999/{cat_id}/training",
                          json={"require_gap": False})
    assert r.status_code == 404
    assert "employee" in r.get_json()["error"].lower()


def test_unknown_category_404(admin_client, temp_app):
    emp_id, _ = _seed_pair(temp_app)
    r = admin_client.post(f"/api/v1/skills/{emp_id}/99999/training",
                          json={"require_gap": False})
    assert r.status_code == 404
    assert "category" in r.get_json()["error"].lower()


def test_unscored_cell_refused_as_gap(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training")
    assert r.status_code == 409
    assert "no competency score" in r.get_json()["error"].lower()


def test_strong_cell_refused_as_gap(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 3)  # "go-to / teaches"
    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training")
    assert r.status_code == 409
    assert "not a gap" in r.get_json()["error"].lower()


def test_force_non_gap_with_require_gap_false(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 3)
    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training",
                          json={"require_gap": False})
    assert r.status_code == 201
    assert r.get_json()["created"] is True


# ── Create from gap ────────────────────────────────────────────────────────


def test_create_training_from_gap_carries_fields(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app, name="Dana Drafter", cat_name="Sheet Production")
    _set_score(temp_app, emp_id, cat_id, 1)  # "supervised" — a gap

    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training")
    assert r.status_code == 201
    body = r.get_json()
    assert body["created"] is True
    tid = body["training_task_id"]
    assert tid > 0

    with temp_app.app_context():
        sess = get_session()
        t = sess.get(TrainingTask, tid)
        assert t is not None
        assert t.trainees == "Dana Drafter"
        assert t.skill_area == "Sheet Production"
        assert "Sheet Production" in t.training_goals
        assert "Dana Drafter" in t.title
        assert t.source == "competency_bridge"
        # Employee FK merged into trainee_ids JSON array.
        import json
        assert emp_id in json.loads(t.trainee_ids)


def test_score_zero_is_a_gap(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 0)  # "can't yet"
    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training")
    assert r.status_code == 201
    assert r.get_json()["created"] is True


def test_overrides_win_over_defaults(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 1)
    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training", json={
        "overrides": {"training_goals": "Custom plan", "priority": "High"},
    })
    assert r.status_code == 201
    tid = r.get_json()["training_task_id"]
    with temp_app.app_context():
        sess = get_session()
        t = sess.get(TrainingTask, tid)
        assert t.training_goals == "Custom plan"
        assert t.priority == "High"
        # Non-overridden fields still carried.
        assert t.trainees != ""


# ── Two-way linkage ────────────────────────────────────────────────────────


def test_linkage_reflected_both_ways(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 1)
    tid = admin_client.post(
        f"/api/v1/skills/{emp_id}/{cat_id}/training").get_json()["training_task_id"]

    with temp_app.app_context():
        sess = get_session()
        # Training side marker.
        training_marker = sess.scalar(select(ActivityLog).where(and_(
            ActivityLog.table_name == "training_tasks",
            ActivityLog.record_id == tid,
            ActivityLog.action == f"linked_competency:{emp_id}:{cat_id}",
        )))
        assert training_marker is not None
        # Cell side marker.
        cell_marker = sess.scalar(select(ActivityLog).where(and_(
            ActivityLog.table_name == "employee_skill_scores",
            ActivityLog.action == f"linked_training:{tid}",
        )))
        assert cell_marker is not None


def test_get_link_summary_lists_training(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 1)
    tid = admin_client.post(
        f"/api/v1/skills/{emp_id}/{cat_id}/training").get_json()["training_task_id"]

    r = admin_client.get(f"/api/v1/skills/{emp_id}/{cat_id}/training")
    assert r.status_code == 200
    body = r.get_json()
    assert body["is_gap"] is True
    assert body["score"] == 1
    ids = [t["id"] for t in body["linked_training"]]
    assert tid in ids


# ── Link existing task ─────────────────────────────────────────────────────


def test_link_existing_training_task(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 1)
    # An existing training task created independently.
    existing = admin_client.post("/api/v1/training_tasks", json={
        "title": "Pre-existing workshop",
    }).get_json()
    eid = existing["id"]

    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training", json={
        "training_task_id": eid,
    })
    assert r.status_code == 200  # linked, not created
    body = r.get_json()
    assert body["created"] is False
    assert body["training_task_id"] == eid

    # Reflected on the link summary.
    summary = admin_client.get(
        f"/api/v1/skills/{emp_id}/{cat_id}/training").get_json()
    assert eid in [t["id"] for t in summary["linked_training"]]


def test_link_nonexistent_training_404(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 1)
    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training", json={
        "training_task_id": 99999,
    })
    assert r.status_code == 404


def test_link_existing_does_not_require_gap(admin_client, temp_app):
    """Linking an existing task is operator intent — no gap gate applies."""
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 3)  # strong, not a gap
    existing = admin_client.post("/api/v1/training_tasks", json={
        "title": "Stretch goal",
    }).get_json()
    r = admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training", json={
        "training_task_id": existing["id"],
    })
    assert r.status_code == 200


# ── Idempotency ────────────────────────────────────────────────────────────


def test_relinking_same_pair_is_idempotent(admin_client, temp_app):
    emp_id, cat_id = _seed_pair(temp_app)
    _set_score(temp_app, emp_id, cat_id, 1)
    existing = admin_client.post("/api/v1/training_tasks", json={
        "title": "Once-linked",
    }).get_json()
    eid = existing["id"]

    admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training",
                      json={"training_task_id": eid})
    admin_client.post(f"/api/v1/skills/{emp_id}/{cat_id}/training",
                      json={"training_task_id": eid})

    with temp_app.app_context():
        sess = get_session()
        cell_markers = sess.scalars(select(ActivityLog).where(and_(
            ActivityLog.table_name == "employee_skill_scores",
            ActivityLog.action == f"linked_training:{eid}",
        ))).all()
        assert len(cell_markers) == 1
        training_markers = sess.scalars(select(ActivityLog).where(and_(
            ActivityLog.table_name == "training_tasks",
            ActivityLog.record_id == eid,
            ActivityLog.action == f"linked_competency:{emp_id}:{cat_id}",
        ))).all()
        assert len(training_markers) == 1

    # And the summary lists it exactly once.
    summary = admin_client.get(
        f"/api/v1/skills/{emp_id}/{cat_id}/training").get_json()
    assert [t["id"] for t in summary["linked_training"]].count(eid) == 1
