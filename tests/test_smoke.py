"""HTTP smoke tests against a running TaskTrack instance.

These mirror scripts/smoke.sh in pytest form so the same checks run from
both `make smoke` (shell) and `make test` (pytest). The whole module
auto-skips when nothing is listening on base_url so CI (and local devs
without gunicorn running) don't see false failures.
"""
import socket
from urllib.parse import urlparse

import pytest
import requests


def _service_reachable(base_url: str) -> bool:
    """Quick TCP probe — true if base_url's host:port accepts a connection."""
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (OSError, ValueError):
        return False


@pytest.fixture(autouse=True)
def _require_live_service(base_url):
    """Skip this entire module when the target isn't up. Lets the same
    test file work in (a) local with gunicorn running, (b) local with
    nothing on :5050, and (c) CI containers that don't ship a service."""
    if not _service_reachable(base_url):
        pytest.skip(
            f"live service at {base_url} not reachable — "
            "smoke tests require a running gunicorn (start with `make run`)"
        )


def test_healthz_returns_ok(base_url):
    r = requests.get(f"{base_url}/healthz", timeout=5)
    assert r.status_code == 200
    assert r.text == "ok"


def test_login_renders_signin_form(base_url):
    r = requests.get(f"{base_url}/login", timeout=5)
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_root_redirects_to_login_when_unauthenticated(base_url):
    r = requests.get(f"{base_url}/", timeout=5, allow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_api_blocks_unauthenticated(base_url):
    r = requests.get(
        f"{base_url}/api/v1/work_tasks", timeout=5, allow_redirects=False
    )
    # @login_required can return either 401 (JSON) or a 302 to /login;
    # both are acceptable evidence that the endpoint is gated.
    assert r.status_code in (401, 302)
