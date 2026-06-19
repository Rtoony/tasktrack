"""Regression tests for the portfolio at-risk count (assessment finding D2).

The portfolio packet builds full ``project_status_report`` payloads only for
the displayed page (≤ ``MAX_PORTFOLIO_LIMIT`` projects), then summarised the
at-risk count over just those built reports. With ~6.9k active projects in
prod that under-reported the headline at-risk count, which is presented as
authoritative.

These tests pin the new behaviour: the summary's ``attention_project_count``
(and the ``attention_scanned_total`` denominator) reflect the ENTIRE active /
filter scope via an efficient aggregate, while full reports are still built
only for the page. The seeds deliberately place at-risk projects PAST the
50-project scan cap so the new count discriminates against the old capped one.
"""
from datetime import datetime, timedelta

from app.db import get_session
from app.models import (
    PersonnelIssue,
    Project,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
)
from app.services.project_reports import (
    MAX_PORTFOLIO_LIMIT,
    portfolio_attention_totals,
    portfolio_project_report,
)


def _seed_many_projects(sess, *, count: int, at_risk_indices: set[int]):
    """Seed ``count`` active projects ordered by project_number.

    Indices in ``at_risk_indices`` get one OPEN, OVERDUE linked item (so the
    per-project brief would mark them ``at_risk``); the rest get only a
    future-dated open item (active, not at-risk). Returns the project numbers
    in order.
    """
    now = datetime.now()
    overdue = (now - timedelta(days=2)).date().isoformat()
    future = (now + timedelta(days=10)).date().isoformat()
    numbers: list[str] = []
    for i in range(count):
        number = f"6000.{i:02d}"
        numbers.append(number)
        proj = Project(
            project_number=number,
            name=f"Scope project {i}",
            client="Acme Water",
            component="Site Improvement Plans",
            display_status="active",
            active=1,
        )
        sess.add(proj)
        sess.flush()
        if i in at_risk_indices:
            sess.add(WorkTask(
                title=f"Overdue CAD item {i}",
                project_number=number,
                project_id=proj.id,
                status="In Progress",
                due_date=overdue,
            ))
        else:
            sess.add(WorkTask(
                title=f"Future CAD item {i}",
                project_number=number,
                project_id=proj.id,
                status="In Progress",
                due_date=future,
            ))
    sess.commit()
    return numbers


def test_attention_count_includes_at_risk_past_scan_cap(temp_app):
    """A project at-risk beyond the 50-project cap still counts in the summary."""
    # Place an at-risk project BEFORE the cap (index 3) and two AFTER it
    # (indices 55 and 69) so the full-set count must exceed the page count.
    at_risk = {3, 55, 69}
    with temp_app.app_context():
        sess = get_session()
        _seed_many_projects(sess, count=70, at_risk_indices=at_risk)

        report = portfolio_project_report(sess, filters={"client": "Acme", "limit": 12})
        summary = report["summary"]

        # The aggregate sees every active project, not just the scanned page.
        assert summary["attention_scanned_total"] == 70
        # True full-set at-risk count — all three, including the two past index 50.
        assert summary["attention_project_count"] == 3

        # The page itself only built full reports for the first `limit` projects
        # (project_number order), so the old per-report tally would have at most
        # the at-risk ones inside that window — strictly fewer than 3 here. This
        # is the discriminating assertion: the fix beats the capped logic.
        built_numbers = {r["project"]["project_number"] for r in report["reports"]}
        old_capped_count = sum(
            1 for r in report["reports"]
            if (r.get("management_brief") or {}).get("attention_level") == "at_risk"
        )
        assert old_capped_count < summary["attention_project_count"]
        # And specifically the past-the-cap at-risk projects were NOT in the
        # built page yet are still counted.
        assert "6000.55" not in built_numbers
        assert "6000.69" not in built_numbers


def test_attention_totals_aggregate_matches_per_project_definition(temp_app):
    """The aggregate counts exactly the projects a per-project report calls
    at_risk: open + overdue, archived/done/calendar excluded, FK-or-number link."""
    now = datetime.now()
    overdue_date = (now - timedelta(days=1)).date().isoformat()
    overdue_dt = (now - timedelta(hours=6)).isoformat(timespec="minutes")
    future_dt = (now + timedelta(days=5)).isoformat(timespec="minutes")

    with temp_app.app_context():
        sess = get_session()

        def _proj(number, name):
            p = Project(project_number=number, name=name, client="Beta",
                        display_status="active", active=1)
            sess.add(p)
            sess.flush()
            return p

        # at_risk via project_work_tasks.due_at (datetime branch)
        p_pwt = _proj("7000.01", "PWT overdue")
        sess.add(ProjectWorkTask(
            title="late", project_name="PWT overdue", project_number=p_pwt.project_number,
            project_id=p_pwt.id, status="In Progress", due_at=overdue_dt,
        ))
        # at_risk via training_tasks.due_date (date branch)
        p_tt = _proj("7000.02", "Training overdue")
        sess.add(TrainingTask(
            title="late", project_number=p_tt.project_number, project_id=p_tt.id,
            status="In Progress", due_date=overdue_date,
        ))
        # at_risk via personnel_issues.follow_up_date
        p_pi = _proj("7000.03", "Capability overdue")
        sess.add(PersonnelIssue(
            person_name="x", issue_description="y", project_number=p_pi.project_number,
            project_id=p_pi.id, status="Observed", follow_up_date=overdue_date,
        ))
        # at_risk linked by project_number ONLY (no FK) — the OR-join must catch it
        p_numlink = _proj("7000.04", "Number-only link")
        sess.add(WorkTask(
            title="late", project_number=p_numlink.project_number, project_id=None,
            status="In Progress", due_date=overdue_date,
        ))

        # NOT at_risk: future due date
        p_future = _proj("7000.10", "Future")
        sess.add(WorkTask(
            title="future", project_number=p_future.project_number, project_id=p_future.id,
            status="In Progress", due_date=future_dt,
        ))
        # NOT at_risk: overdue but DONE (Complete excluded for work_tasks)
        p_done = _proj("7000.11", "Done late")
        sess.add(WorkTask(
            title="done", project_number=p_done.project_number, project_id=p_done.id,
            status="Complete", due_date=overdue_date,
        ))
        # NOT at_risk: overdue but ARCHIVED (#38)
        p_arch = _proj("7000.12", "Archived late")
        sess.add(ProjectWorkTask(
            title="arch", project_name="Archived late", project_number=p_arch.project_number,
            project_id=p_arch.id, status="In Progress", due_at=overdue_dt, archived_at=now,
        ))
        # NOT at_risk: blank due date
        p_blank = _proj("7000.13", "No due date")
        sess.add(WorkTask(
            title="blank", project_number=p_blank.project_number, project_id=p_blank.id,
            status="In Progress", due_date="",
        ))
        sess.commit()

        totals = portfolio_attention_totals(sess, {"client": "Beta"}, now=now)
        assert totals["scanned_total"] == 8  # all active Beta projects
        assert totals["at_risk"] == 4        # the four real at-risk ones only

        # And via the public report builder (filtered to at_risk): the headline
        # count is the full-set 4, even though only matching reports are shown.
        report = portfolio_project_report(
            sess, filters={"client": "Beta", "attention_level": "at_risk", "limit": 12},
        )
        assert report["summary"]["attention_project_count"] == 4
        assert report["summary"]["attention_scanned_total"] == 8
        shown = {r["project"]["project_number"] for r in report["reports"]}
        assert shown == {"7000.01", "7000.02", "7000.03", "7000.04"}


def test_attention_total_is_independent_of_scan_cap_constant(temp_app):
    """Sanity: the seeded population exceeds the scan cap, so this regression
    genuinely exercises the past-the-cap path rather than a tiny set."""
    assert MAX_PORTFOLIO_LIMIT == 50
    with temp_app.app_context():
        sess = get_session()
        _seed_many_projects(sess, count=60, at_risk_indices={58})
        totals = portfolio_attention_totals(sess, {"client": "Acme"}, now=datetime.now())
        assert totals["scanned_total"] == 60
        assert totals["at_risk"] == 1
