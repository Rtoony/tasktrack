"""HTTP smoke tests against a running TaskTrack instance.

These mirror scripts/smoke.sh in pytest form so the same checks run from
both `make smoke` (shell) and `make test` (pytest). Phase 1A.2 will add
in-process unit tests with an isolated DB; until then these are the
primary regression net.
"""
import requests


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
        f"{base_url}/api/work_tasks", timeout=5, allow_redirects=False
    )
    # @login_required can return either 401 (JSON) or a 302 to /login;
    # both are acceptable evidence that the endpoint is gated.
    assert r.status_code in (401, 302)
