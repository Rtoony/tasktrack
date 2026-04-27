"""Pytest configuration for TaskTrack.

Two flavors of tests live here:

- HTTP smoke tests (`test_smoke.py`) hit the running gunicorn on
  :5050 via `requests`. They mirror `scripts/smoke.sh` in pytest form
  and require the live service.

- In-process tests (`test_app_factory.py`) use the Flask test client
  against an isolated temp SQLite. They do not need the live service.

Phase 1A.2 limitation: because create_app() configures the module-level
Flask singleton (the package split is Phase 1A.3), tests cannot run two
apps in parallel. Each in-process test gets a fresh DB file but shares
the same Flask instance with sibling tests. Run sequentially.
"""
import os

import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("TASKTRACK_BASE_URL", "http://127.0.0.1:5050")


@pytest.fixture
def temp_app(tmp_path):
    """Build the Flask app against an isolated temp SQLite DB."""
    db_path = tmp_path / "test_tracker.db"

    # Import lazily so module-level setup uses the temp path from the start.
    from app import create_app, init_db

    init_db(str(db_path))
    flask_app = create_app(db_path=str(db_path))
    flask_app.config["TESTING"] = True
    yield flask_app


@pytest.fixture
def client(temp_app):
    """Flask test client backed by the temp DB."""
    return temp_app.test_client()
