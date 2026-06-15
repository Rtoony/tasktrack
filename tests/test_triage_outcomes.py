"""Triage Phase 3 — Triage Outcomes report.

Proves the outcomes report computes, over assigned inbox items:
- volume per source (rule:<template> / model) and per chosen target
- target accuracy (suggested_table == chosen target)
- field accuracy (drafted field kept verbatim in the final record)
- time-to-assignment (created_at -> updated_at proxy)
and the edge cases (unparseable suggestion skipped, un-promoted excluded,
empty corpus, window filter), plus the JSON + CSV endpoints.
"""
import json
from datetime import datetime, timedelta

from app.db import get_session
from app.models import InboxItem
from app.services.tickets import create_direct_record
from app.services.triage_outcomes import (
    OUTCOMES_CSV_FIELDS,
    triage_outcomes_csv,
    triage_outcomes_report,
)

# ── seeding helpers ─────────────────────────────────────────────────────────

def _seed_assigned(sess, *, source_model, suggested_target, chosen_target,
                   drafted_fields, final_fields, created=None, updated=None,
                   title="seed item"):
    """Create a WorkTask-style final record + an inbox item that was
    suggested then promoted to it, so the report has a full pair to score."""
    rec_id, err = create_direct_record(
        sess, chosen_target, dict(final_fields), source_name="test-seed",
    )
    assert err is None, err
    suggestion = {
        "target_table": suggested_target,
        "category": None,
        "confidence": "high",
        "fields": drafted_fields,
        "model": source_model,
        "rationale": "seed",
    }
    now = datetime.now()
    item = InboxItem(
        title=title,
        body="",
        source="test",
        status="Archived",
        suggested_table=suggested_target,
        suggestion_json=json.dumps(suggestion),
        suggested_at=now,
        promoted_to_table=chosen_target,
        promoted_to_id=rec_id,
        created_at=created or now,
        updated_at=updated or now,
    )
    sess.add(item)
    sess.flush()
    return item, rec_id


# ── service: accuracy + field-edit + grouping ──────────────────────────────

def test_target_and_field_accuracy(temp_app):
    with temp_app.app_context():
        sess = get_session()
        # Item A: suggested work_tasks, chosen work_tasks (correct target);
        # drafted title kept, drafted priority changed by the human.
        _seed_assigned(
            sess,
            source_model="rule:cad-prefix",
            suggested_target="work_tasks",
            chosen_target="work_tasks",
            drafted_fields={"title": "Fix lisp", "priority": "Low"},
            final_fields={"title": "Fix lisp", "priority": "High"},
        )
        # Item B: same source, suggested work_tasks but chosen personal_items
        # (wrong target). One drafted field; the final personal_items row has
        # a different title so it's an edit.
        _seed_assigned(
            sess,
            source_model="rule:cad-prefix",
            suggested_target="work_tasks",
            chosen_target="personal_items",
            drafted_fields={"title": "Order toner"},
            final_fields={"title": "Buy toner", "category": "Office"},
        )
        sess.commit()

        report = triage_outcomes_report(sess)

    summary = report["summary"]
    assert summary["considered"] == 2
    assert summary["volume"] == 2
    # one of two correct target
    assert summary["target_correct"] == 1
    assert summary["target_accuracy"] == 0.5
    # fields compared: A title(kept)+priority(edit) = 2 (1 kept); B title(edit)=1
    # total compared 3, kept 1 -> 1/3
    assert summary["fields_compared"] == 3
    assert summary["fields_kept"] == 1
    assert round(summary["field_accuracy"], 4) == round(1 / 3, 4)

    src = {r["source"]: r for r in report["by_source"]}
    assert "rule:cad-prefix" in src
    assert src["rule:cad-prefix"]["volume"] == 2
    assert src["rule:cad-prefix"]["target_accuracy"] == 0.5

    tgt = {r["target_table"]: r for r in report["by_target"]}
    assert tgt["work_tasks"]["volume"] == 1
    assert tgt["personal_items"]["volume"] == 1


def test_time_to_assignment(temp_app):
    with temp_app.app_context():
        sess = get_session()
        base = datetime.now() - timedelta(days=1)
        _seed_assigned(
            sess,
            source_model="rule:cad-prefix",
            suggested_target="work_tasks",
            chosen_target="work_tasks",
            drafted_fields={"title": "t"},
            final_fields={"title": "t"},
            created=base,
            updated=base + timedelta(seconds=300),
        )
        sess.commit()
        report = triage_outcomes_report(sess)

    tta = report["summary"]["time_to_assignment_seconds"]
    assert tta["count"] == 1
    assert tta["mean"] == 300.0
    assert tta["median"] == 300.0


def test_grouping_separates_sources(temp_app):
    with temp_app.app_context():
        sess = get_session()
        for model in ("rule:cad-prefix", "rule:cad-prefix", "gemini-flash"):
            _seed_assigned(
                sess, source_model=model, suggested_target="work_tasks",
                chosen_target="work_tasks",
                drafted_fields={"title": "x"}, final_fields={"title": "x"},
            )
        sess.commit()
        report = triage_outcomes_report(sess)

    src = {r["source"]: r["volume"] for r in report["by_source"]}
    assert src == {"rule:cad-prefix": 2, "gemini-flash": 1}
    # sorted by volume desc
    assert report["by_source"][0]["source"] == "rule:cad-prefix"


# ── edge cases ──────────────────────────────────────────────────────────────

def test_empty_corpus(temp_app):
    with temp_app.app_context():
        sess = get_session()
        report = triage_outcomes_report(sess)
    assert report["summary"]["considered"] == 0
    assert report["summary"]["volume"] == 0
    assert report["summary"]["target_accuracy"] is None
    assert report["summary"]["field_accuracy"] is None
    assert report["by_source"] == []
    assert report["by_target"] == []


def test_unpromoted_item_excluded(temp_app):
    """A suggested-but-not-yet-assigned item is not an outcome yet."""
    with temp_app.app_context():
        sess = get_session()
        item = InboxItem(
            title="pending", body="", source="test", status="New",
            suggested_table="work_tasks",
            suggestion_json=json.dumps({"target_table": "work_tasks",
                                        "fields": {"title": "x"},
                                        "model": "rule:cad-prefix"}),
            suggested_at=datetime.now(),
            promoted_to_table="",  # not assigned
        )
        sess.add(item)
        sess.commit()
        report = triage_outcomes_report(sess)
    assert report["summary"]["considered"] == 0


def test_unparseable_suggestion_skipped(temp_app):
    with temp_app.app_context():
        sess = get_session()
        rec_id, err = create_direct_record(
            sess, "work_tasks", {"title": "real"}, source_name="t")
        assert err is None
        item = InboxItem(
            title="bad json", body="", source="test", status="Archived",
            suggested_table="work_tasks",
            suggestion_json="{not valid json",
            suggested_at=datetime.now(),
            promoted_to_table="work_tasks", promoted_to_id=rec_id,
            created_at=datetime.now(), updated_at=datetime.now(),
        )
        sess.add(item)
        sess.commit()
        report = triage_outcomes_report(sess)
    assert report["summary"]["considered"] == 0
    assert report["summary"]["skipped_unparseable"] == 1


def test_window_filter_excludes_old(temp_app):
    with temp_app.app_context():
        sess = get_session()
        old = datetime.now() - timedelta(days=400)
        _seed_assigned(
            sess, source_model="rule:cad-prefix", suggested_target="work_tasks",
            chosen_target="work_tasks", drafted_fields={"title": "old"},
            final_fields={"title": "old"}, created=old, updated=old,
        )
        sess.commit()
        report = triage_outcomes_report(sess, days=30)
    assert report["summary"]["considered"] == 0


# ── CSV ─────────────────────────────────────────────────────────────────────

def test_csv_shape(temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_assigned(
            sess, source_model="rule:cad-prefix", suggested_target="work_tasks",
            chosen_target="work_tasks", drafted_fields={"title": "x"},
            final_fields={"title": "x"},
        )
        sess.commit()
        report = triage_outcomes_report(sess)

    csv_text = triage_outcomes_csv(report)
    lines = csv_text.strip().splitlines()
    assert lines[0] == ",".join(OUTCOMES_CSV_FIELDS)
    # overall + 1 source + 1 target = 3 data rows
    assert len(lines) == 4
    assert lines[1].startswith("overall,ALL,")


# ── endpoints ───────────────────────────────────────────────────────────────

def test_json_endpoint(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_assigned(
            sess, source_model="rule:cad-prefix", suggested_target="work_tasks",
            chosen_target="work_tasks", drafted_fields={"title": "x"},
            final_fields={"title": "x"},
        )
        sess.commit()
    r = auth_client.get("/api/v1/reports/triage-outcomes")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["summary"]["considered"] == 1
    assert body["by_source"][0]["source"] == "rule:cad-prefix"


def test_csv_endpoint(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_assigned(
            sess, source_model="rule:cad-prefix", suggested_target="work_tasks",
            chosen_target="work_tasks", drafted_fields={"title": "x"},
            final_fields={"title": "x"},
        )
        sess.commit()
    r = auth_client.get("/api/v1/reports/triage-outcomes.csv")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"]
    assert b"group_kind" in r.data


def test_json_endpoint_requires_login(client):
    assert client.get("/api/v1/reports/triage-outcomes").status_code == 401


def test_html_page_renders(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        _seed_assigned(
            sess, source_model="rule:cad-prefix", suggested_target="work_tasks",
            chosen_target="work_tasks", drafted_fields={"title": "x"},
            final_fields={"title": "x"},
        )
        sess.commit()
    r = auth_client.get("/reports/triage-outcomes")
    assert r.status_code == 200
    assert b"Triage Outcomes" in r.data
    assert b"rule:cad-prefix" in r.data
