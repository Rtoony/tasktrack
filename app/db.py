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
# Reads the persistent Flask secret out of `app_settings` (kept here
# rather than in models.py because it runs during create_app, before
# the request-scoped session machinery is ready). On first run (empty
# DB after `flask db upgrade`) this generates a fresh key and inserts
# it.

def get_secret_key(db_path=None) -> str:
    path = db_path or DB_PATH
    db = sqlite3.connect(path)
    try:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'secret_key'"
        ).fetchone()
        if row:
            return row[0]
        # First boot of a fresh DB — seed a key.
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
