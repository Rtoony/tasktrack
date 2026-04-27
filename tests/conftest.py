"""Pytest configuration for TaskTrack.

Two flavors of tests live here:

- HTTP smoke tests (`test_smoke.py`) hit the running gunicorn on
  :5050 via `requests`. They mirror `scripts/smoke.sh` in pytest form
  and require the live service.

- In-process tests (`test_app_factory.py`) use the Flask test client
  against an isolated temp SQLite. They do not need the live service.
  Schema is built fresh by Alembic per test.

Phase 1A.3+ note: create_app() builds a fresh Flask instance per call
so in-process tests are properly isolated. The SQLAlchemy engine is
also rebuilt per call because each create_app pushes its own DB_PATH;
the lazy `_ensure_engine` re-binds on first use after `_engine = None`.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.models import Base


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("TASKTRACK_BASE_URL", "http://127.0.0.1:5050")


def _build_schema(db_path: str) -> None:
    """Build the schema in a fresh SQLite via SQLAlchemy metadata.create_all.

    Equivalent to `alembic upgrade head` against an empty DB but
    avoids spawning a subprocess per test. Stays in sync with models
    by definition.
    """
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


@pytest.fixture
def temp_app(tmp_path, monkeypatch):
    """Build the Flask app against an isolated temp SQLite DB."""
    db_path = tmp_path / "test_tracker.db"
    _build_schema(str(db_path))

    # Reset the module-global engine so create_app's _ensure_engine()
    # picks up the temp DB on first session use.
    import app.db
    monkeypatch.setattr(app.db, "_engine", None)
    monkeypatch.setattr(app.db, "_session_factory", None)

    from app import create_app

    flask_app = create_app(db_path=str(db_path))
    flask_app.config["TESTING"] = True
    yield flask_app


@pytest.fixture
def client(temp_app):
    """Flask test client backed by the temp DB."""
    return temp_app.test_client()
