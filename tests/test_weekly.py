"""Phase-6 weekly snapshot + route tests.

Aggregator is pure-data so we exercise it directly; route layer is
exercised via the Flask test client for auth gating + admin-only
buckets + days clamping.
"""
from datetime import UTC, date, datetime, timedelta

from app.db import get_session
from app.models import (
    ActivityLog,
    CalendarEvent,
    Employee,
    EmployeeSkillScore,
    PersonnelIssue,
    ProjectWorkTask,
    SkillCategory,
    WorkTask,
)
from app.services.weekly import (
    BREAKDOWN_KEY_LIMIT,
    UNASSIGNED_LABEL,
    weekly_snapshot,
)

# ── Pure-data aggregator ─────────────────────────────────────────────────


def test_snapshot_returns_expected_keys(temp_app):
    with temp_app.app_context():
        sess = get_session()
        snap = weekly_snapshot(sess, days=7)
    assert set(snap.keys()) >= {"since", "until", "days", "totals", "buckets",
                                  "incidents_recent"}
    assert snap["days"] == 7
    assert set(snap["totals"].keys()) == {"created", "completed",
                                          "active_now", "overdue_now"}


def test_snapshot_buckets_one_per_allowed_table(temp_app):
    with temp_app.app_context():
        sess = get_session()
        snap = weekly_snapshot(sess, days=7)
    # All generic tracker tables get a bucket.
    assert set(snap["buckets"].keys()) == {
        "work_tasks", "project_work_tasks", "training_tasks",
        "personnel_issues", "inbox_items", "personal_items",
        "calendar_events",
    }


def test_created_count_matches_seed(temp_app):
    """Insert two work_tasks NOW; snapshot's work_tasks bucket should
    show created=2 and 2 items in items_created."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="A"))
        sess.add(WorkTask(title="B"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    work = snap["buckets"]["work_tasks"]
    assert work["created"] == 2
    assert {item["title"] for item in work["items_created"]} == {"A", "B"}


def test_completed_count(temp_app):
    """W2: 'completed this window' is derived from the activity_log status-change
    history. A row that transitioned to a done status in-window (with a logged
    status_change, as every API write produces) is counted."""
    with temp_app.app_context():
        sess = get_session()
        row = WorkTask(title="Old task", status="In Progress")
        sess.add(row)
        sess.flush()
        row.status = "Complete"
        sess.add(ActivityLog(
            table_name="work_tasks", record_id=row.id, action="status_change",
            field_name="status", old_value="In Progress", new_value="Complete",
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    assert snap["buckets"]["work_tasks"]["completed"] == 1


def test_completed_excludes_old_done_item_edited_in_window(temp_app):
    """W2 regression: a long-completed item merely EDITED inside the window (which
    bumps updated_at) must NOT re-list as 'completed this week'. The old updated_at
    heuristic re-counted it and inflated the headline; the activity_log derivation
    doesn't, because the done-transition was logged before the window."""
    with temp_app.app_context():
        sess = get_session()
        row = WorkTask(title="Long done", status="Complete")
        sess.add(row)
        sess.flush()
        # Completion happened 60 days ago — outside the 7-day window.
        sess.add(ActivityLog(
            table_name="work_tasks", record_id=row.id, action="status_change",
            field_name="status", old_value="In Progress", new_value="Complete",
            created_at=datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=60),
        ))
        sess.commit()
        # A later in-window edit bumps updated_at (the old false-positive trigger).
        row.updated_at = datetime.now(tz=UTC).replace(tzinfo=None)
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    assert snap["buckets"]["work_tasks"]["completed"] == 0


def test_active_excludes_done(temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="open-a", status="In Progress"))
        sess.add(WorkTask(title="open-b", status="On Hold"))
        sess.add(WorkTask(title="done-c", status="Complete"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    assert snap["buckets"]["work_tasks"]["active_now"] == 2


def test_overdue_counts(temp_app):
    """A row with status active and due_date in the past should count as
    overdue_now in its bucket and roll up to totals."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="late", status="In Progress",
                          due_date=yesterday))
        sess.add(WorkTask(title="on-time", status="In Progress",
                          due_date=""))
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    assert snap["buckets"]["work_tasks"]["overdue_now"] == 1
    assert snap["totals"]["overdue_now"] >= 1


def test_old_row_outside_window_not_in_created(temp_app):
    """A row created OUTSIDE the window must not appear in items_created."""
    with temp_app.app_context():
        sess = get_session()
        old = WorkTask(title="ancient")
        sess.add(old)
        sess.commit()
        # Backdate it 14 days.
        old.created_at = datetime.utcnow() - timedelta(days=14)
        sess.commit()

        snap = weekly_snapshot(sess, days=7)
    titles = {i["title"] for i in snap["buckets"]["work_tasks"]["items_created"]}
    assert "ancient" not in titles


def test_incidents_recent_includes_personnel_rows(temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(PersonnelIssue(
            person_name="Alice",
            issue_description="needs help",
            severity="High",
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, include_admin=True)
    assert any(i["person_name"] == "Alice"
               for i in snap["incidents_recent"])


def test_zero_person_incidents_show_up_in_weekly(temp_app):
    """Phase-5.5 0-person incidents (person_name None) shouldn't break
    the weekly view — `(no person)` fallback applies."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(PersonnelIssue(
            person_name=None,
            issue_description="Process gap on standards rollout",
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, include_admin=True)
    assert any(i["person_name"] == "(no person)"
               for i in snap["incidents_recent"])


def test_weekly_redacts_capability_narratives_for_non_admin(temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(PersonnelIssue(
            person_name="Sensitive Employee",
            issue_description="Sensitive weekly narrative",
            severity="High",
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, include_admin=False)
    incidents = snap["incidents_recent"]
    assert incidents[0]["person_name"] == "Restricted"
    assert incidents[0]["issue_description"] == "Capability narrative restricted"
    assert incidents[0]["redacted"] is True
    titles = {i["title"] for i in snap["buckets"]["personnel_issues"]["items_created"]}
    assert "Capability note (restricted)" in titles
    assert "Sensitive weekly narrative" not in str(snap)
    assert "Sensitive Employee" not in str(snap)



def test_weekly_calendar_past_meetings_are_not_overdue(temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(CalendarEvent(
            title="Past calendar meeting",
            event_type="meeting",
            start_at=(datetime.now(tz=UTC) - timedelta(days=1)).replace(tzinfo=None).isoformat(timespec="minutes"),
            status="scheduled",
            created_by_user_id=1,
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, user_id=1)
    cal = snap["buckets"]["calendar_events"]
    assert cal["active_now"] == 1
    assert cal["overdue_now"] == 0


def test_weekly_hides_private_calendar_events_from_other_users(temp_app):
    with temp_app.app_context():
        sess = get_session()
        start = (datetime.now(tz=UTC) + timedelta(days=1)).replace(tzinfo=None).isoformat(timespec="minutes")
        sess.add(CalendarEvent(
            title="Shared weekly event",
            event_type="meeting",
            start_at=start,
            visibility="internal",
            created_by_user_id=1,
        ))
        sess.add(CalendarEvent(
            title="Private weekly event",
            event_type="prep",
            start_at=start,
            visibility="private",
            created_by_user_id=1,
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, user_id=2)
    cal = snap["buckets"]["calendar_events"]
    titles = {item["title"] for item in cal["items_created"]}
    assert "Shared weekly event" in titles
    assert "Private weekly event" not in titles
    assert cal["active_now"] == 1


# ── Admin-only skill score changes ──────────────────────────────────────


def test_skill_score_changes_only_when_include_admin(temp_app):
    """include_admin gates the skill_score_changes bucket."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="Emp"))
        sess.add(SkillCategory(slug="x", name="X"))
        sess.commit()
        score = EmployeeSkillScore(employee_id=1, category_id=1, score=5.0)
        sess.add(score)
        sess.flush()
        sess.add(ActivityLog(
            table_name="employee_skill_scores",
            record_id=score.id,
            action="score_set",
            field_name="score",
            old_value="",
            new_value="5.0",
        ))
        sess.commit()

        non_admin = weekly_snapshot(sess, days=7, include_admin=False)
        admin = weekly_snapshot(sess, days=7, include_admin=True)
    assert "skill_score_changes" not in non_admin
    assert "skill_score_changes" in admin
    assert len(admin["skill_score_changes"]) >= 1


# ── Route layer ──────────────────────────────────────────────────────────


def test_json_endpoint_requires_login(client):
    r = client.get("/api/v1/weekly")
    assert r.status_code == 401


def test_html_page_requires_login(client):
    r = client.get("/weekly", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_json_endpoint_for_logged_in_user(auth_client):
    r = auth_client.get("/api/v1/weekly?days=14")
    assert r.status_code == 200
    body = r.get_json()
    assert body["days"] == 14
    # Non-admin: admin-only bucket absent.
    assert "skill_score_changes" not in body


def test_json_endpoint_admin_includes_score_bucket(admin_client):
    r = admin_client.get("/api/v1/weekly?days=7")
    assert r.status_code == 200
    body = r.get_json()
    assert "skill_score_changes" in body


def test_html_page_renders(auth_client):
    r = auth_client.get("/weekly")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "Week in Review" in html
    assert "Created" in html
    assert "Completed" in html


def test_weekly_html_redacts_capability_narratives_for_non_admin(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(PersonnelIssue(
            person_name="Sensitive Employee",
            issue_description="Sensitive weekly html narrative",
            severity="High",
        ))
        sess.commit()

    r = auth_client.get("/weekly")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Capability narrative restricted" in html
    assert "Sensitive weekly html narrative" not in html
    assert "Sensitive Employee" not in html


def test_days_arg_clamps_to_max_90(auth_client):
    r = auth_client.get("/api/v1/weekly?days=9999")
    assert r.status_code == 200
    assert r.get_json()["days"] == 90


def test_days_arg_clamps_to_min_1(auth_client):
    r = auth_client.get("/api/v1/weekly?days=-5")
    assert r.status_code == 200
    assert r.get_json()["days"] == 1


def test_days_arg_garbage_defaults_to_7(auth_client):
    r = auth_client.get("/api/v1/weekly?days=banana")
    assert r.status_code == 200
    assert r.get_json()["days"] == 7


# ── Status / assignee / age breakdowns (P2-3 report-engine extension) ─────


def test_breakdown_absent_by_default(temp_app):
    """The default payload must NOT carry a `breakdown` block — keeps the
    standard shape and its existing consumers untouched."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="x", status="In Progress"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    assert "breakdown" not in snap["buckets"]["work_tasks"]


def test_breakdown_by_status_counts_all_rows(temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="a", status="In Progress"))
        sess.add(WorkTask(title="b", status="In Progress"))
        sess.add(WorkTask(title="c", status="On Hold"))
        sess.add(WorkTask(title="d", status="Complete"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, breakdown=True)
    by_status = snap["buckets"]["work_tasks"]["breakdown"]["by_status"]
    assert by_status == {"In Progress": 2, "On Hold": 1, "Complete": 1}


def test_breakdown_by_assignee_only_counts_open(temp_app):
    """by_assignee groups OPEN rows by the table's assignee field; done
    rows are excluded, missing names roll into UNASSIGNED_LABEL."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="a", status="In Progress", requested_by="Dana"))
        sess.add(WorkTask(title="b", status="On Hold", requested_by="Dana"))
        sess.add(WorkTask(title="c", status="In Progress", requested_by=""))
        sess.add(WorkTask(title="d", status="Complete", requested_by="Dana"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, breakdown=True)
    by_assignee = snap["buckets"]["work_tasks"]["breakdown"]["by_assignee"]
    assert by_assignee == {"Dana": 2, UNASSIGNED_LABEL: 1}


def test_breakdown_uses_engineer_field_for_project_tasks(temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(ProjectWorkTask(title="p1", status="In Progress", engineer="Lee"))
        sess.add(ProjectWorkTask(title="p2", status="In Progress", engineer="Lee"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, breakdown=True)
    bd = snap["buckets"]["project_work_tasks"]["breakdown"]
    assert bd["by_assignee"] == {"Lee": 2}


def test_breakdown_no_assignee_dim_for_inbox(temp_app):
    """inbox_items has no owner column → no by_assignee key, but the other
    two dimensions are still present."""
    with temp_app.app_context():
        sess = get_session()
        snap = weekly_snapshot(sess, days=7, breakdown=True)
    bd = snap["buckets"]["inbox_items"]["breakdown"]
    assert "by_assignee" not in bd
    assert "by_status" in bd
    assert "age_buckets" in bd


def test_breakdown_age_buckets_only_count_open(temp_app):
    """A fresh open row lands in 0-2d; an old open row lands in 31d+;
    a Complete row is excluded from age buckets entirely."""
    with temp_app.app_context():
        sess = get_session()
        fresh = WorkTask(title="fresh", status="In Progress")
        old = WorkTask(title="old", status="In Progress")
        done = WorkTask(title="done", status="Complete")
        sess.add_all([fresh, old, done])
        sess.commit()
        old.created_at = datetime.utcnow() - timedelta(days=60)
        sess.commit()
        snap = weekly_snapshot(sess, days=7, breakdown=True)
    ages = snap["buckets"]["work_tasks"]["breakdown"]["age_buckets"]
    assert ages["0-2d"] == 1
    assert ages["31d+"] == 1
    assert sum(ages.values()) == 2  # the Complete row is not counted


def test_breakdown_redacts_personnel_assignee_for_non_admin(temp_app):
    """Sensitive trackers must not leak person names through the assignee
    rollup when the caller isn't admin."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(PersonnelIssue(
            person_name="Private Person",
            issue_description="sensitive",
            status="Open",
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, breakdown=True, include_admin=False)
    by_assignee = snap["buckets"]["personnel_issues"]["breakdown"]["by_assignee"]
    assert "Private Person" not in by_assignee
    assert by_assignee.get("Restricted") == 1
    assert "Private Person" not in str(snap)


def test_breakdown_shows_personnel_assignee_for_admin(temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(PersonnelIssue(
            person_name="Named Person",
            issue_description="sensitive",
            status="Open",
        ))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, breakdown=True, include_admin=True)
    by_assignee = snap["buckets"]["personnel_issues"]["breakdown"]["by_assignee"]
    assert by_assignee.get("Named Person") == 1


def test_breakdown_caps_distinct_assignees(temp_app):
    """More distinct assignees than BREAKDOWN_KEY_LIMIT collapses the tail
    into a single 'Other (N)' entry so the JSON stays bounded."""
    extra = 8
    with temp_app.app_context():
        sess = get_session()
        for i in range(BREAKDOWN_KEY_LIMIT + extra):
            sess.add(WorkTask(title=f"t{i}", status="In Progress",
                              requested_by=f"person-{i:03d}"))
        sess.commit()
        snap = weekly_snapshot(sess, days=7, breakdown=True)
    by_assignee = snap["buckets"]["work_tasks"]["breakdown"]["by_assignee"]
    assert len(by_assignee) == BREAKDOWN_KEY_LIMIT + 1
    other_key = next(k for k in by_assignee if k.startswith("Other ("))
    assert by_assignee[other_key] == extra


def test_breakdown_route_opt_in(auth_client):
    base = auth_client.get("/api/v1/weekly").get_json()
    assert "breakdown" not in next(iter(base["buckets"].values()))
    bd = auth_client.get("/api/v1/weekly?breakdown=1").get_json()
    assert "breakdown" in next(iter(bd["buckets"].values()))


def test_breakdown_html_renders_rollups(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(WorkTask(title="rollup-task", status="In Progress",
                          requested_by="Sam"))
        sess.commit()
    r = auth_client.get("/weekly?breakdown=1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "By status" in html
    assert "Open by assignee" in html
    assert "Open by age" in html
    assert "Sam" in html
