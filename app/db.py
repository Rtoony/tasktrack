"""SQLite plumbing — SQLAlchemy session factory + secret-key helper.

Schema is owned by Alembic now (see `migrations/`). Fresh deploys run
`flask db upgrade`. The legacy init_db / ensure_column /
normalize_ticket_tables runtime mutators were removed in Phase 1D-2j;
the live DB was stamped at the baseline revision in Phase 1D-1.

`DB_PATH` stays exported at module scope. `get_session()` builds a
process-global SQLAlchemy engine bound to whichever DB_PATH the live
Flask app's config points at; sessions are request-scoped via Flask's
`g` and closed by the teardown registered in create_app.
"""
import os
import secrets
import sqlite3

from flask import current_app, g
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# Project root is one level up from this package.
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tracker.db",
)


# ── SQLAlchemy session ────────────────────────────────────────────────────

_engine = None
_session_factory = None


def _ensure_engine() -> None:
    """Lazily build the engine + session factory for the current app's DB."""
    global _engine, _session_factory
    if _engine is not None:
        return
    path = current_app.config.get("DB_PATH", DB_PATH)
    _engine = create_engine(
        f"sqlite:///{path}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        # Multiple gunicorn workers share this file; without a busy
        # timeout a concurrent write raises "database is locked"
        # immediately instead of waiting for the lock to clear.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    _session_factory = sessionmaker(
        bind=_engine,
        future=True,
        # expire_on_commit=False keeps attributes accessible after a
        # commit — important for routes that commit then return the
        # row in a JSON response.
        expire_on_commit=False,
    )


def get_session() -> Session:
    """Return a request-scoped SQLAlchemy session."""
    if "session" not in g:
        _ensure_engine()
        g.session = _session_factory()
    return g.session


def close_session(exc) -> None:
    """Roll back on error, then close. Registered via teardown_appcontext."""
    sess = g.pop("session", None)
    if sess is None:
        return
    try:
        if exc is not None:
            sess.rollback()
    finally:
        sess.close()


# ── Secret-key helper ─────────────────────────────────────────────────────
#
# Resolution order:
#   1. TASKTRACK_SECRET_KEY env var — injected from the Nexus vault by
#      `nexus-svc-inject` into /dev/shm/nexus-env-collab-tracker.
#      THIS IS THE CANONICAL SOURCE post-2026-05-20 vault migration.
#   2. app_settings.secret_key in the SQLite DB — DEPRECATED. Logs a
#      WARNING when used. Kept only so a vault outage doesn't take down
#      the app entirely; remove the row once you're confident vault
#      injection is stable.
#   3. Fresh generation, persisted to the DB. First-boot path for tests,
#      fresh clones, and emergency recovery. Logs an ERROR so you notice.

import logging  # noqa: E402  -- intentional in-module placement near use

_SECRET_LOG = logging.getLogger("tasktrack.secret_key")


def get_secret_key(db_path=None) -> str:
    env_key = (os.environ.get("TASKTRACK_SECRET_KEY") or "").strip()
    if env_key:
        return env_key

    path = db_path or DB_PATH
    db = sqlite3.connect(path)
    try:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'secret_key'"
        ).fetchone()
        if row:
            _SECRET_LOG.warning(
                "secret_key fell back to app_settings table — "
                "TASKTRACK_SECRET_KEY env var was not set. Check vault "
                "injection (nexus-svc-inject) before trusting sessions."
            )
            return row[0]
        # First boot of a fresh DB AND no vault key — seed one. This
        # keeps fresh deploys (e.g. tests, new dev clones) working
        # without forcing the vault round-trip. Logged loud because
        # post-2026-05-20 vault migration this should never fire in prod.
        _SECRET_LOG.error(
            "minting fresh secret_key into app_settings — neither "
            "TASKTRACK_SECRET_KEY env nor existing DB row was present. "
            "Sessions before this restart are now invalid. Add the new "
            "value to the Nexus - TaskTrack vault item."
        )
        key = secrets.token_hex(32)
        db.execute(
            "INSERT INTO app_settings (key, value) VALUES ('secret_key', ?)",
            (key,),
        )
        db.commit()
        return key
    finally:
        db.close()


def get_app_setting(setting_key: str, default_value: str = "", db_path=None) -> str:
    """Read a row from app_settings. Used during startup before session is wired."""
    path = db_path or DB_PATH
    db = sqlite3.connect(path)
    try:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = ?", (setting_key,),
        ).fetchone()
        return row[0] if row else default_value
    finally:
        db.close()
