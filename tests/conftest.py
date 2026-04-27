"""Pytest configuration for TaskTrack.

Phase 1A.1 ships only HTTP-level smoke tests against the running service.
A proper isolated `client` fixture (temp SQLite + flask init-db + Flask
test client) lands in Phase 1A.2 once the app-factory pattern lets us
construct the app against a swappable database path.
"""
import os

import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("TASKTRACK_BASE_URL", "http://127.0.0.1:5050")
