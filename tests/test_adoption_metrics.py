from datetime import datetime, timedelta

from app.db import get_session
from app.models import (
    ActivityLog,
    CalendarEvent,
    Comment,
    InboxItem,
    ProjectWorkTask,
)
from app.services.adoption_metrics import adoption_metrics


def test_adoption_metrics_counts_trial_evidence(temp_app):
    now = datetime(2026, 5, 30, 9, 0, 0)
    with temp_app.app_context():
        sess = get_session()
        for offset in range(8):
            ts = now - timedelta(days=offset)
            sess.add(ActivityLog(
                table_name="project_work_tasks",
                record_id=offset + 1,
                action="create",
                user_name="Tester",
                created_at=ts,
            ))
        sess.add(InboxItem(
            title="Incoming item",
            status="New",
            created_at=now - timedelta(days=1),
        ))
        sess.add(ProjectWorkTask(
            title="Project follow-up",
            project_number="1234.56",
            status="In Progress",
            created_at=now - timedelta(days=1),
        ))
        sess.add(Comment(
            table_name="project_work_tasks",
            record_id=1,
            user_name="Tester",
            body="Followed up",
            created_at=now - timedelta(days=1),
        ))
        sess.add(CalendarEvent(
            title="Project meeting",
            start_at=(now + timedelta(days=1)).isoformat(),
            project_number="1234.56",
            created_at=now - timedelta(days=1),
        ))
        sess.commit()

        packet = adoption_metrics(sess, days=14, now=now)

    assert packet["summary"]["active_days"] == 8
    assert packet["summary"]["open_inbox"] == 1
    assert packet["summary"]["project_linked_records"] == 2
    assert packet["summary"]["future_calendar_events"] == 1
    assert packet["targets"]["activity_days"]["met"] is True
    assert packet["targets"]["comments"]["met"] is True


def test_adoption_metrics_clamps_window(temp_app):
    now = datetime(2026, 5, 30, 9, 0, 0)
    with temp_app.app_context():
        packet = adoption_metrics(get_session(), days=999, now=now)

    assert packet["window"]["days"] == 90


def test_adoption_metrics_cli_outputs_summary(temp_app):
    result = temp_app.test_cli_runner().invoke(
        args=["adoption-metrics", "--days", "14"]
    )

    assert result.exit_code == 0
    assert "TaskTrack adoption metrics (14 days)" in result.output
    assert "targets_met:" in result.output

