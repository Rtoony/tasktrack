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
    SkillCategory,
    WorkTask,
)
from app.services.weekly import weekly_snapshot

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
    """An UPDATE that sets status to Complete bumps updated_at; the
    aggregator's heuristic should pick this up."""
    with temp_app.app_context():
        sess = get_session()
        row = WorkTask(title="Old task", status="In Progress")
        sess.add(row)
        sess.commit()
        # Mark it complete inside the window.
        row.status = "Complete"
        row.updated_at = datetime.now(tz=UTC).replace(tzinfo=None)
        sess.commit()
        snap = weekly_snapshot(sess, days=7)
    assert snap["buckets"]["work_tasks"]["completed"] == 1


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
