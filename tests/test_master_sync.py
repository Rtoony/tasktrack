"""Tests for the master-list auto-sync prototype.

Two layers:

1. **Importer-level**: re-uses the in-memory XLSX/KMZ fixtures from
   `test_import_projects` and exercises the new transition tracking +
   vanish detection inside `run_import()`.

2. **Wrapper-level**: writes throwaway XLSX/KMZ files to a temp dir,
   invokes `sync_master_if_changed.run_sync()` against them, and
   asserts the state file is created, the second run is a no-op, and
   --force re-runs.

The Telegram side of the wrapper is mocked: we set the notify env vars
to empty so `notify_master_sync` short-circuits to stdout and the
subprocess returns 0 without trying to reach api.telegram.org.
"""
from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.db import get_session
from app.models import Project

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import import_projects_from_master as importer  # noqa: E402
import sync_master_if_changed as syncer  # noqa: E402


# ── Tiny fixture builders ─────────────────────────────────────────────────


def _write_master_xlsx(path: Path, rows: list[tuple]) -> None:
    """rows is a list of (project_number, name, status, client) tuples."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Project List"])
    ws.append(["Brelje & Race"])
    ws.append([])
    ws.append(["Project", "Name", "Status", "Start\nDate", "Dormant\nDate",
               "Client\nName", "Principal", "Component"])
    for pn, name, status, client in rows:
        ws.append([pn, name, status, None, None, client, "Principal", "Component"])
    wb.save(path)


def _write_locator_kmz(path: Path, placemarks: list[tuple]) -> None:
    """placemarks is a list of (name, lng, lat, color) tuples where
    color is one of yellow/red/green/blue (mapped to *-pushpin.png)."""
    color_to_icon = {
        "yellow": "ylw-pushpin.png",
        "red":    "red-pushpin.png",
        "green":  "grn-pushpin.png",
        "blue":   "blue-pushpin.png",
    }
    style_block = "".join(
        f'<Style id="{color}"><IconStyle><Icon><href>'
        f'http://maps.google.com/mapfiles/kml/pushpin/{icon}</href></Icon>'
        f'</IconStyle></Style>'
        for color, icon in color_to_icon.items()
    )
    pms = "".join(
        f'<Placemark><name>{name}</name>'
        f'<styleUrl>#{color}</styleUrl>'
        f'<Point><coordinates>{lng},{lat},0</coordinates></Point>'
        f'</Placemark>'
        for name, lng, lat, color in placemarks
    )
    kml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        '<name>Project Locator.kmz</name>'
        f'{style_block}'
        f'<Folder><name>1 - 499</name>{pms}</Folder>'
        '</Document></kml>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml)


def _db_url(temp_app) -> str:
    return f"sqlite:///{temp_app.config['DB_PATH']}"


# ── Importer-level: transition tracking + vanish detection ────────────────


def test_first_import_marks_all_as_created(tmp_path, temp_app):
    xlsx = tmp_path / "master.xlsx"
    kmz = tmp_path / "locator.kmz"
    _write_master_xlsx(xlsx, [
        (100.00, "Alpha", "Active", "Client A"),
        (200.00, "Beta",  "Dormant", "Client B"),
    ])
    _write_locator_kmz(kmz, [
        ("100.00", -122.0, 38.0, "yellow"),
        ("200.00", -122.5, 38.5, "yellow"),
    ])
    report = importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    assert set(report["created"]) == {"100.00", "200.00"}
    assert report["updated"] == []
    assert report["vanished"] == []
    # last_seen_in_master_at should be stamped on both rows.
    with temp_app.app_context():
        sess = get_session()
        for pn in ("100.00", "200.00"):
            p = sess.scalar(select(Project).where(Project.project_number == pn))
            assert p.last_seen_in_master_at
            assert p.vanished_from_master_at == ""


def test_second_import_unchanged_yields_unchanged_transitions(tmp_path, temp_app):
    xlsx = tmp_path / "master.xlsx"
    kmz = tmp_path / "locator.kmz"
    _write_master_xlsx(xlsx, [(100.00, "Alpha", "Active", "Client A")])
    _write_locator_kmz(kmz, [("100.00", -122.0, 38.0, "yellow")])

    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)
    report2 = importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)
    assert report2["created"] == []
    assert report2["updated"] == []
    assert "100.00" in report2["unchanged"]


def test_metadata_change_flagged_as_updated(tmp_path, temp_app):
    xlsx = tmp_path / "master.xlsx"
    kmz = tmp_path / "locator.kmz"
    _write_master_xlsx(xlsx, [(100.00, "Alpha", "Active", "Client A")])
    _write_locator_kmz(kmz, [("100.00", -122.0, 38.0, "yellow")])
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    # Operator edits the spreadsheet — client name corrected.
    _write_master_xlsx(xlsx, [(100.00, "Alpha", "Active", "Client A (renamed)")])
    report = importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)
    assert report["updated"] == ["100.00"]
    assert report["created"] == []


def test_vanished_project_marked_dormant(tmp_path, temp_app):
    xlsx = tmp_path / "master.xlsx"
    kmz = tmp_path / "locator.kmz"
    _write_master_xlsx(xlsx, [
        (100.00, "Alpha", "Active", "Client A"),
        (200.00, "Beta",  "Active", "Client B"),
    ])
    _write_locator_kmz(kmz, [
        ("100.00", -122.0, 38.0, "yellow"),
        ("200.00", -122.5, 38.5, "yellow"),
    ])
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    # Beta is removed from the master in a later revision.
    _write_master_xlsx(xlsx, [(100.00, "Alpha", "Active", "Client A")])
    report = importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)
    assert report["vanished"] == ["200.00"]

    with temp_app.app_context():
        sess = get_session()
        beta = sess.scalar(select(Project).where(Project.project_number == "200.00"))
        assert beta.display_status == "dormant"
        assert beta.vanished_from_master_at
        alpha = sess.scalar(select(Project).where(Project.project_number == "100.00"))
        assert alpha.vanished_from_master_at == ""


def test_returning_project_clears_vanished_flag(tmp_path, temp_app):
    xlsx = tmp_path / "master.xlsx"
    kmz = tmp_path / "locator.kmz"
    _write_master_xlsx(xlsx, [
        (100.00, "Alpha", "Active", "Client A"),
        (200.00, "Beta",  "Active", "Client B"),
    ])
    _write_locator_kmz(kmz, [
        ("100.00", -122.0, 38.0, "yellow"),
        ("200.00", -122.5, 38.5, "yellow"),
    ])
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    # Remove Beta, importer marks it vanished.
    _write_master_xlsx(xlsx, [(100.00, "Alpha", "Active", "Client A")])
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    # Beta comes back in the next revision.
    _write_master_xlsx(xlsx, [
        (100.00, "Alpha", "Active", "Client A"),
        (200.00, "Beta",  "Active", "Client B"),
    ])
    report = importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)
    assert "200.00" in report["returned"]

    with temp_app.app_context():
        sess = get_session()
        beta = sess.scalar(select(Project).where(Project.project_number == "200.00"))
        assert beta.vanished_from_master_at == ""
        # display_status should reflect the master's current value (Active)
        assert beta.display_status == "active"


def test_kmz_only_orphan_not_marked_vanished(tmp_path, temp_app):
    """An orphan project (KMZ pin but no master-list row) should never
    be auto-dormanted on later sync runs that also lack the master row,
    because it was never expected to be there."""
    xlsx = tmp_path / "master.xlsx"
    kmz = tmp_path / "locator.kmz"
    _write_master_xlsx(xlsx, [(100.00, "Alpha", "Active", "Client A")])
    # 9999.00 has a pin but no Excel row → orphan
    _write_locator_kmz(kmz, [
        ("100.00",  -122.0, 38.0, "yellow"),
        ("9999.00", -122.5, 38.5, "yellow"),
    ])
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    # Second run with identical inputs — orphan should still be present
    # and not be flagged as vanished.
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)
    with temp_app.app_context():
        sess = get_session()
        orphan = sess.scalar(select(Project).where(Project.project_number == "9999.00"))
        assert orphan is not None
        assert orphan.vanished_from_master_at == ""


# ── Wrapper-level: change detection, state file, idempotency ──────────────


@pytest.fixture
def source_dir_with_files(tmp_path):
    """Build a USB-shaped source directory with one XLSX + one KMZ."""
    src = tmp_path / "source"
    src.mkdir()
    _write_master_xlsx(
        src / "Master List - Numeric 052126.xlsx",
        [(100.00, "Alpha", "Active", "Client A")],
    )
    _write_locator_kmz(src / "Project Locator.kmz", [
        ("100.00", -122.0, 38.0, "yellow"),
    ])
    return src


def test_sync_first_run_triggers_import_and_writes_state(
    tmp_path, temp_app, source_dir_with_files, monkeypatch,
):
    state = tmp_path / "state.json"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CLAUDE_CHAT_ID", raising=False)

    rc = syncer.run_sync(
        source_dir=source_dir_with_files,
        db_url=_db_url(temp_app),
        state_path=state,
        dry_run=False,
        force=False,
    )
    assert rc == 0
    assert state.is_file()
    payload = json.loads(state.read_text())
    assert payload["xlsx_sha256"]
    assert payload["kmz_sha256"]
    assert payload["last_report"]["projects_upserted"] == 1


def test_sync_second_run_unchanged_skips_import(
    tmp_path, temp_app, source_dir_with_files, monkeypatch,
):
    state = tmp_path / "state.json"
    syncer.run_sync(
        source_dir=source_dir_with_files,
        db_url=_db_url(temp_app),
        state_path=state, dry_run=False, force=False,
    )
    state_before = state.read_text()

    # Same files; the second call should not re-run the importer (we
    # detect this by the state file not having moved on).
    rc = syncer.run_sync(
        source_dir=source_dir_with_files,
        db_url=_db_url(temp_app),
        state_path=state, dry_run=False, force=False,
    )
    assert rc == 0
    assert state.read_text() == state_before


def test_sync_detects_changed_xlsx_and_reruns(
    tmp_path, temp_app, source_dir_with_files, monkeypatch,
):
    state = tmp_path / "state.json"
    syncer.run_sync(
        source_dir=source_dir_with_files,
        db_url=_db_url(temp_app),
        state_path=state, dry_run=False, force=False,
    )
    before = json.loads(state.read_text())

    # Edit the XLSX — different sha now.
    _write_master_xlsx(
        source_dir_with_files / "Master List - Numeric 052126.xlsx",
        [
            (100.00, "Alpha",   "Active", "Client A"),
            (200.00, "Bravo!!", "Active", "Client B"),
        ],
    )
    syncer.run_sync(
        source_dir=source_dir_with_files,
        db_url=_db_url(temp_app),
        state_path=state, dry_run=False, force=False,
    )
    after = json.loads(state.read_text())
    assert after["xlsx_sha256"] != before["xlsx_sha256"]
    assert after["last_report"]["created_count"] == 1


def test_sync_force_reruns_when_hashes_match(
    tmp_path, temp_app, source_dir_with_files, monkeypatch,
):
    state = tmp_path / "state.json"
    syncer.run_sync(
        source_dir=source_dir_with_files,
        db_url=_db_url(temp_app),
        state_path=state, dry_run=False, force=False,
    )
    before_run_at = json.loads(state.read_text())["last_run_at"]

    syncer.run_sync(
        source_dir=source_dir_with_files,
        db_url=_db_url(temp_app),
        state_path=state, dry_run=False, force=True,
    )
    after_run_at = json.loads(state.read_text())["last_run_at"]
    assert after_run_at != before_run_at


def test_sync_missing_source_dir_exits_cleanly(tmp_path, temp_app):
    state = tmp_path / "state.json"
    nope = tmp_path / "does-not-exist"
    with pytest.raises(SystemExit) as exc:
        syncer.run_sync(
            source_dir=nope, db_url=_db_url(temp_app),
            state_path=state, dry_run=False, force=False,
        )
    assert "NAS unmounted" in str(exc.value) or "source dir not present" in str(exc.value)
    assert not state.exists()


def test_sync_dry_run_does_not_touch_db_or_state(
    tmp_path, temp_app, source_dir_with_files,
):
    state = tmp_path / "state.json"
    rc = syncer.run_sync(
        source_dir=source_dir_with_files,
        db_url=_db_url(temp_app),
        state_path=state, dry_run=True, force=False,
    )
    assert rc == 0
    assert not state.exists()
    with temp_app.app_context():
        sess = get_session()
        # No projects should have landed.
        assert sess.query(Project).count() == 0


# ── Notifier-level: digest formatting ─────────────────────────────────────


def test_digest_includes_all_transition_categories():
    from notify_master_sync import build_digest

    text = build_digest({
        "run_at": "2026-05-22T03:30:00+00:00",
        "created": ["A.01", "A.02"],
        "updated": ["B.01"],
        "vanished": ["V.01"],
        "returned": ["R.01"],
        "orphan_projects_created": 1,
        "sites_inserted": 3,
        "kmz_unique_numbers": 3,
    })
    assert "2 new project" in text
    assert "1 metadata update" in text
    assert "1 vanished → dormant" in text
    assert "1 returned" in text
    assert "1 KMZ-only orphan" in text
    assert "A.01" in text
    assert "V.01" in text


def test_digest_no_changes_says_pins_only():
    from notify_master_sync import build_digest

    text = build_digest({
        "run_at": "2026-05-22T03:30:00+00:00",
        "created": [], "updated": [], "vanished": [], "returned": [],
        "orphan_projects_created": 0,
        "sites_inserted": 5392, "kmz_unique_numbers": 4503,
    })
    assert "No project-level changes" in text
    assert "5392 site pin" in text


# ── HTTP: /api/v1/projects/sync-status ────────────────────────────────────


def test_sync_status_never_run(auth_client, tmp_path, monkeypatch):
    """No state file → endpoint reports never_run rather than 500."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    r = auth_client.get("/api/v1/projects/sync-status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["state"] == "never_run"


def test_sync_status_reads_state_file(auth_client, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    state_dir = tmp_path / "tasktrack"
    state_dir.mkdir()
    payload = {
        "schema_version": 1,
        "last_run_at": "2026-05-22T03:30:00+00:00",
        "xlsx_sha256": "abc123",
        "kmz_sha256":  "def456",
        "last_report": {
            "projects_upserted": 6144,
            "created_count": 3,
            "updated_count": 12,
            "vanished_count": 1,
            "returned_count": 0,
            "vanished_sample": ["1899.04"],
        },
    }
    (state_dir / "master-sync.json").write_text(json.dumps(payload))

    r = auth_client.get("/api/v1/projects/sync-status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["state"] == "ok"
    assert body["xlsx_sha256"] == "abc123"
    assert body["last_report"]["created_count"] == 3


def test_sync_status_requires_auth(client):
    r = client.get("/api/v1/projects/sync-status")
    assert r.status_code == 401


def test_admin_dashboard_exposes_project_sync_status_card(admin_client):
    r = admin_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Project List Sync" in html
    assert 'id="dash-sync-status"' in html
    assert "loadProjectSyncStatus" in html
