"""Phase-5.5 tests: multi-person incidents + the /intake/incident form.

Verifies the three-mode person identification:
- 0 people    (process / equipment incidents)
- 1 person    (classic capability gap)
- many people (team-wide gap, multiple staff in the same incident)

Also checks the auth-gated intake form and that the submission hub
surfaces the new card with its sign-in marker.
"""
import json

from sqlalchemy import select

from app.db import get_session
from app.models import Employee, InboxItem, PersonnelIssue
from app.services.tickets import _resolve_person_ids

# ── Zero-person incidents ─────────────────────────────────────────────────


def test_zero_person_incident_create(auth_client):
    """An incident may have no people identified at all (process gap,
    equipment failure, anonymous report)."""
    r = auth_client.post("/api/v1/personnel_issues", json={
        "issue_description": "Plotter jammed during 3am batch print",
    })
    assert r.status_code in (200, 201)
    rec = r.get_json()
    assert rec["person_name"] in (None, "")
    assert rec["person_ids"] == "[]"
    assert rec["person_id"] is None


def test_zero_person_incident_does_not_require_person_name(auth_client):
    """Drop of `person_name` from `required` is honoured by the
    generic CRUD validator."""
    r = auth_client.post("/api/v1/personnel_issues", json={
        # no person_name at all
        "issue_description": "Anonymous safety observation",
        "severity": "High",
    })
    assert r.status_code in (200, 201)


# ── Single-person incidents (backward compat) ────────────────────────────


def test_single_person_text_resolves_to_fk(auth_client, temp_app):
    """Type one name → enrich_with_fks should populate person_ids with
    the matching employee id and seed person_id."""
    with temp_app.app_context():
        sess = get_session()
        emp = Employee(display_name="Alice Smith")
        sess.add(emp)
        sess.commit()
        emp_id = emp.id

    r = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Alice Smith",
        "issue_description": "Layer standard violation",
    })
    rec = r.get_json()
    assert rec["person_name"] == "Alice Smith"
    assert json.loads(rec["person_ids"]) == [emp_id]
    assert rec["person_id"] == emp_id


def test_single_person_unknown_name_keeps_text_drops_fk(auth_client):
    """An unmatched name stays in person_name but person_ids stays empty."""
    r = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Nobody Recognized",
        "issue_description": "Spilled coffee on the plotter",
    })
    rec = r.get_json()
    assert rec["person_name"] == "Nobody Recognized"
    assert rec["person_ids"] == "[]"
    assert rec["person_id"] is None


# ── Multi-person incidents ───────────────────────────────────────────────


def test_multi_person_comma_split(auth_client, temp_app):
    """Comma-separated names resolve into a multi-element person_ids
    list. Order matches input order. Duplicates are de-duped."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="Alice"))
        sess.add(Employee(display_name="Bob"))
        sess.add(Employee(display_name="Charlie"))
        sess.commit()
        ids_by_name = {
            e.display_name: e.id
            for e in sess.scalars(select(Employee)).all()
        }

    r = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Alice, Bob, Charlie",
        "issue_description": "Team-wide gap on the standards rollout",
    })
    rec = r.get_json()
    ids = json.loads(rec["person_ids"])
    assert ids == [ids_by_name["Alice"], ids_by_name["Bob"],
                   ids_by_name["Charlie"]]
    # person_id is set to the first match for backward compat.
    assert rec["person_id"] == ids_by_name["Alice"]


def test_multi_person_semicolon_split_also_works(auth_client, temp_app):
    """The enrichment helper accepts semicolons as separators too."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="Dee"))
        sess.add(Employee(display_name="Eve"))
        sess.commit()

    r = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Dee; Eve",
        "issue_description": "Coaching needed on both",
    })
    ids = json.loads(r.get_json()["person_ids"])
    assert len(ids) == 2


def test_multi_person_unmatched_names_dropped(auth_client, temp_app):
    """Names that don't match an employee are silently skipped — the
    matching ones still land in person_ids."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="Known"))
        sess.commit()
        known_id = sess.scalar(
            select(Employee.id).where(Employee.display_name == "Known")
        )

    r = auth_client.post("/api/v1/personnel_issues", json={
        "person_name": "Known, Stranger Danger",
        "issue_description": "x",
    })
    ids = json.loads(r.get_json()["person_ids"])
    assert ids == [known_id]


def test_multi_person_explicit_list_beats_text_parse(auth_client, temp_app):
    """If the caller supplies a non-empty person_ids JSON, the enrich
    helper trusts it as authoritative and skips the comma parse."""
    with temp_app.app_context():
        sess = get_session()
        sess.add(Employee(display_name="One"))
        sess.add(Employee(display_name="Two"))
        sess.commit()
        ids_by_name = {
            e.display_name: e.id
            for e in sess.scalars(select(Employee)).all()
        }

    r = auth_client.post("/api/v1/personnel_issues", json={
        # Text says "One" but explicit list overrides with Two.
        "person_name": "One",
        "person_ids": json.dumps([ids_by_name["Two"]]),
        "issue_description": "Explicit picker beats text",
    })
    rec = r.get_json()
    assert json.loads(rec["person_ids"]) == [ids_by_name["Two"]]
    # person_id mirrors first element of person_ids.
    assert rec["person_id"] == ids_by_name["Two"]


# ── _resolve_person_ids unit ─────────────────────────────────────────────


def test_resolve_seeds_empty_list_when_text_and_list_both_blank(temp_app):
    """If both person_name and person_ids are blank, the helper writes
    a valid empty JSON list so the column never holds NULL or ''."""
    with temp_app.app_context():
        sess = get_session()
        rec = PersonnelIssue(
            person_name="",
            person_ids="",  # whatever defaulted/empty state
            issue_description="no people",
        )
        sess.add(rec)
        sess.flush()
        _resolve_person_ids(sess, rec)
        assert rec.person_ids == "[]"


def test_resolve_keeps_explicit_list_when_text_blank(temp_app):
    """Explicit person_ids stays authoritative even if person_name was
    cleared. The UI's multi-select beats the comma-parse fallback."""
    with temp_app.app_context():
        sess = get_session()
        rec = PersonnelIssue(
            person_name="",
            person_ids='[1, 2, 3]',
            issue_description="explicit picker only",
        )
        sess.add(rec)
        sess.flush()
        _resolve_person_ids(sess, rec)
        assert rec.person_ids == '[1, 2, 3]'  # unchanged


# ── Intake form (/intake/incident) ──────────────────────────────────────


def test_intake_incident_requires_login(client):
    r = client.get("/intake/incident", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_intake_incident_redirects_for_logged_in_user(auth_client):
    r = auth_client.get("/intake/incident", follow_redirects=False)
    assert r.status_code == 302
    assert "/intake/request?type=problem" in r.headers["Location"]


def test_problem_submit_creates_reviewable_inbox_item(auth_client, temp_app):
    r = auth_client.post("/api/v1/intake/submit", json={
        "type": "problem",
        "fields": {
            "details": "Submitted via the unified intake form",
            "involved": "Form Submitter Subject",
            "skill": "Workflow",
        },
        "severity": "High",
    })
    assert r.status_code == 201
    inbox_id = r.get_json()["inbox_id"]

    with temp_app.app_context():
        sess = get_session()
        rec = sess.get(InboxItem, inbox_id)
        assert rec is not None
        assert rec.title == "Submitted via the unified intake form"
        assert rec.source == "web-form"
        assert rec.priority == "High"
        assert rec.status == "New"
        assert "personnel_issues" in rec.body
        assert sess.scalar(
            select(PersonnelIssue)
            .where(PersonnelIssue.issue_description == "Submitted via the unified intake form")
        ) is None


# ── Submission hub ──────────────────────────────────────────────────────


def test_hub_lists_incident_form(auth_client):
    r = auth_client.get("/intake")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "Incident Report" in html
    assert "Printable PDF / reMarkable Intake Packet" in html
    assert "/intake/printable" in html
    assert "Print PDF Packet" in html
    assert "Submit CAD changes, fixes, or manager follow-up requests" in html
    assert "Routes to CAD Dev" in html
    assert "What happens after submission" in html
    assert "built-in method copy" not in html
    assert "/intake/request?type=problem" in html
    # The sign-in marker should be visible on the auth-gated card.
    assert "sign-in" in html.lower()
