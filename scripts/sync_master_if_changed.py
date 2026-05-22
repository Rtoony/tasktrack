#!/usr/bin/env python3
"""TaskTrack master-list auto-sync wrapper.

The cron-callable entry point for the automated project-master import.
Daily systemd timer fires this; it decides whether anything actually
changed since the last run and, if so, invokes the existing importer.

Flow:
  1. Resolve the source directory. Looks at (in order):
        --source-dir CLI arg
        TASKTRACK_MASTER_SOURCE_DIR env var
        the prototype default: /media/rtoony/13FB-6205
  2. Locate the latest `Master List - Numeric*.xlsx` (by mtime) and a
     stably-named `Project Locator.kmz` inside that dir.
  3. Compute sha256 of both files.
  4. Compare against the previous run's hashes in the state file
     ($XDG_STATE_HOME/tasktrack/master-sync.json, mode 0600). If both
     unchanged → exit 0 silently (timer noise stays quiet).
  5. If either changed → call the importer with --report-json pointing
     at a tempfile, then update the state file with the new hashes +
     run timestamp + a compact summary of the report.
  6. If Telegram is wired (env vars TELEGRAM_BOT_TOKEN +
     TELEGRAM_CLAUDE_CHAT_ID), pipe the report through
     notify_master_sync.py so the operator sees the digest. Failures
     here log but don't break the sync.

Prototype mode (current): source files live on a USB drive. The
TASKTRACK_MASTER_SOURCE_DIR env var is the single thing that needs to
change when the firm's NAS share is mounted at /mnt/synology-eng-data
(or wherever) — no code change required.

Run modes:
  # Default: read live config, do the work
  python3 scripts/sync_master_if_changed.py

  # Dry-run: parse files and decide whether a change exists, but do
  # not touch the DB or write the state file. Used by the systemd
  # service's --dry-run smoke before enabling the timer.
  python3 scripts/sync_master_if_changed.py --dry-run

  # Force: ignore stored hashes and re-import even if files haven't
  # changed. Useful for redoing an import after a schema migration.
  python3 scripts/sync_master_if_changed.py --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

LOG = logging.getLogger("tasktrack.master_sync")

# Prototype default. The deployment will set TASKTRACK_MASTER_SOURCE_DIR
# to the real mount point (e.g. /mnt/synology-eng-data/master-list).
_DEFAULT_SOURCE_DIR = "/media/rtoony/13FB-6205"

_XLSX_GLOB = "Master List - Numeric*.xlsx"
_KMZ_NAME = "Project Locator.kmz"


def _state_path() -> Path:
    """State file location. Honors $XDG_STATE_HOME, falls back to
    ~/.local/state. Same place the rest of TaskTrack's runtime state
    will live as we add it."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "tasktrack" / "master-sync.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_state(state_path: Path) -> dict:
    if not state_path.is_file():
        return {}
    try:
        return json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("state file at %s unreadable (%s); treating as empty", state_path, exc)
        return {}


def _write_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.chmod(0o600)
    tmp.replace(state_path)


def _resolve_sources(source_dir: Path) -> tuple[Path, Path]:
    """Locate the XLSX + KMZ inside source_dir. Returns absolute paths.
    Raises SystemExit on ambiguity or missing files so the timer surfaces
    operator mistakes (zero matches, multiple XLSXes, etc)."""
    if not source_dir.is_dir():
        raise SystemExit(f"source dir not present (NAS unmounted?): {source_dir}")

    xlsx_candidates = sorted(
        source_dir.glob(_XLSX_GLOB), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not xlsx_candidates:
        raise SystemExit(
            f"no Master List XLSX found in {source_dir}; "
            f"expected file matching {_XLSX_GLOB!r}"
        )
    if len(xlsx_candidates) > 3:
        # Three is generous — the office admin sometimes keeps a backup
        # of last month's spreadsheet alongside the current one. More
        # than three is dirty laundry and the operator should be told.
        raise SystemExit(
            f"found {len(xlsx_candidates)} XLSX files matching {_XLSX_GLOB!r} "
            f"in {source_dir} — clean up the source directory and retry"
        )
    xlsx_path = xlsx_candidates[0]

    kmz_path = source_dir / _KMZ_NAME
    if not kmz_path.is_file():
        raise SystemExit(
            f"missing KMZ at {kmz_path}; expected exactly one "
            f"{_KMZ_NAME!r} in the source directory"
        )

    return xlsx_path, kmz_path


def _compact_summary(report: dict) -> dict:
    """Produce the shrunk-down summary that lives on the state file —
    full project-number lists are great for the Telegram digest but a
    waste of bytes in the persisted state. Keep totals + at most 10
    sample project numbers per category for traceability."""
    sample = lambda lst: lst[:10]
    return {
        "excel_rows":              report.get("excel_rows", 0),
        "kmz_pins_total":          report.get("kmz_pins_total", 0),
        "projects_upserted":       report.get("projects_upserted", 0),
        "orphan_projects_created": report.get("orphan_projects_created", 0),
        "sites_inserted":          report.get("sites_inserted", 0),
        "created_count":           len(report.get("created", [])),
        "updated_count":           len(report.get("updated", [])),
        "unchanged_count":         len(report.get("unchanged", [])),
        "vanished_count":          len(report.get("vanished", [])),
        "returned_count":          len(report.get("returned", [])),
        "created_sample":          sample(report.get("created", [])),
        "vanished_sample":         sample(report.get("vanished", [])),
        "returned_sample":         sample(report.get("returned", [])),
    }


def _invoke_importer(xlsx: Path, kmz: Path, *, db_url: str | None,
                     report_json: Path) -> dict:
    """Shell out to import_projects_from_master.py. Why subprocess
    instead of an in-process call? It keeps the wrapper free of every
    transitive dep the importer pulls in (openpyxl, sqlalchemy boot)
    when the common path is "nothing changed — exit fast". Trades a few
    hundred ms of fork for clean separation."""
    cmd = [
        sys.executable,
        str(HERE / "import_projects_from_master.py"),
        "--xlsx", str(xlsx),
        "--kmz",  str(kmz),
        "--report-json", str(report_json),
    ]
    if db_url:
        cmd.extend(["--db", db_url])

    LOG.info("invoking importer: %s", " ".join(cmd))
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        LOG.error("importer failed rc=%d stderr=%s",
                  completed.returncode, completed.stderr.strip())
        raise SystemExit(
            f"importer exited {completed.returncode}: {completed.stderr.strip()}"
        )
    LOG.info("importer ok")
    if not report_json.is_file():
        raise SystemExit(
            f"importer did not write --report-json {report_json}; refusing "
            f"to update state without a confirmed import outcome"
        )
    return json.loads(report_json.read_text())


def _notify_telegram(report: dict, *, dry_run: bool) -> None:
    """Best-effort Telegram digest. Failure here doesn't break the sync —
    the DB is already updated and the state file already written; the
    operator just doesn't see the chat message and the importer's stdout
    is still captured by journald for after-the-fact inspection."""
    if dry_run:
        LOG.info("dry-run: skipping Telegram notification")
        return
    # No-op when there's nothing interesting to report.
    if not any(report.get(k) for k in ("created", "updated", "vanished", "returned")) \
            and report.get("orphan_projects_created", 0) == 0:
        LOG.info("nothing to report — skipping Telegram")
        return

    notify_script = HERE / "notify_master_sync.py"
    if not notify_script.is_file():
        LOG.warning("notify_master_sync.py not present; skipping")
        return

    try:
        subprocess.run(
            [sys.executable, str(notify_script)],
            input=json.dumps(report),
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        LOG.info("telegram digest sent")
    except subprocess.CalledProcessError as exc:
        LOG.warning("telegram digest failed rc=%d stderr=%s",
                    exc.returncode, (exc.stderr or "").strip())
    except subprocess.TimeoutExpired:
        LOG.warning("telegram digest timed out")
    except Exception as exc:  # noqa: BLE001
        LOG.warning("telegram digest raised: %s", exc)


def run_sync(*, source_dir: Path, db_url: str | None,
             state_path: Path, dry_run: bool, force: bool) -> int:
    """Do one sync cycle. Returns the int exit code."""
    xlsx_path, kmz_path = _resolve_sources(source_dir)
    xlsx_hash = _sha256_file(xlsx_path)
    kmz_hash = _sha256_file(kmz_path)

    state = _load_state(state_path)
    unchanged = (
        state.get("xlsx_sha256") == xlsx_hash
        and state.get("kmz_sha256") == kmz_hash
    )

    if unchanged and not force:
        LOG.info("no source changes since %s; nothing to do",
                 state.get("last_run_at", "(never)"))
        if dry_run:
            print("UNCHANGED — would skip import")
        return 0

    if dry_run:
        why = "force" if force else "hash differs"
        print(f"WOULD IMPORT ({why}):")
        print(f"  xlsx: {xlsx_path}  sha256={xlsx_hash[:12]}…")
        print(f"  kmz:  {kmz_path}  sha256={kmz_hash[:12]}…")
        print(f"  state: {state_path}")
        return 0

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmpf:
        report_json = Path(tmpf.name)

    try:
        report = _invoke_importer(xlsx_path, kmz_path,
                                  db_url=db_url, report_json=report_json)
    finally:
        # Keep the temp report around if the importer failed (already
        # raised SystemExit); on success we've slurped it into `report`.
        report_json.unlink(missing_ok=True)

    new_state = {
        "schema_version":  1,
        "last_run_at":     datetime.now(timezone.utc).isoformat(),
        "xlsx_path":       str(xlsx_path),
        "xlsx_sha256":     xlsx_hash,
        "kmz_path":        str(kmz_path),
        "kmz_sha256":      kmz_hash,
        "last_report":     _compact_summary(report),
    }
    _write_state(state_path, new_state)
    LOG.info("state updated → %s", state_path)

    _notify_telegram(report, dry_run=dry_run)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir", type=Path, default=None,
        help="Directory holding the master XLSX + KMZ. Falls back to "
             "$TASKTRACK_MASTER_SOURCE_DIR, then a prototype default.",
    )
    parser.add_argument(
        "--db", default=None,
        help="DB URL override (default: live TaskTrack DB).",
    )
    parser.add_argument(
        "--state-file", type=Path, default=None,
        help="State file path override.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would happen without touching the DB or state.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignore stored hashes and re-import even if files match.",
    )
    parser.add_argument(
        "--log-level", default=os.environ.get("TASKTRACK_LOG_LEVEL", "INFO"),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    source_dir = args.source_dir or Path(
        os.environ.get("TASKTRACK_MASTER_SOURCE_DIR", _DEFAULT_SOURCE_DIR)
    )
    state_path = args.state_file or _state_path()

    return run_sync(
        source_dir=source_dir,
        db_url=args.db,
        state_path=state_path,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
