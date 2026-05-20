"""Phase-5 health pill tests.

Probes are mocked at the requests level — we never hit a real LiteLLM
or MinIO in CI. Verifies:
- Aggregation logic (worst-of-all, ignoring n/a)
- Each probe's status mapping
- Endpoint shape + auth gating
- Endpoint never blocks (cache-only read path)
- Background thread is NOT started under pytest
"""
from unittest.mock import patch

import pytest
import requests

from app.services import health


@pytest.fixture(autouse=True)
def _reset_probe_state():
    """Each test starts from a known-empty snapshot."""
    with health._PROBE_LOCK:
        health._PROBE_STATE["components"] = []
        health._PROBE_STATE["overall"] = "n/a"
        health._PROBE_STATE["checked_at"] = None
    yield


# ── Aggregation ──────────────────────────────────────────────────────────


def test_aggregate_all_ok_is_ok():
    components = [
        {"status": "ok"}, {"status": "ok"}, {"status": "ok"},
    ]
    assert health._aggregate(components) == "ok"


def test_aggregate_one_warn_is_warn():
    components = [
        {"status": "ok"}, {"status": "warn"}, {"status": "ok"},
    ]
    assert health._aggregate(components) == "warn"


def test_aggregate_one_error_beats_warn():
    components = [
        {"status": "warn"}, {"status": "error"}, {"status": "ok"},
    ]
    assert health._aggregate(components) == "error"


def test_aggregate_ignores_na():
    """n/a (probe not configured) doesn't drag overall below ok."""
    components = [{"status": "ok"}, {"status": "n/a"}, {"status": "ok"}]
    assert health._aggregate(components) == "ok"


def test_aggregate_all_na_returns_ok():
    """Nothing configured → ok rather than n/a so the dot shows green
    instead of grey by default. (Edge case; in practice at least vault
    will return real status.)"""
    components = [{"status": "n/a"}, {"status": "n/a"}]
    assert health._aggregate(components) == "ok"


# ── HTTP probe mapping ───────────────────────────────────────────────────


def test_http_probe_returns_na_when_url_blank():
    p = health._probe_http("x", "X", "")
    assert p["status"] == "n/a"
    assert "url" in p["detail"].lower()


def test_http_probe_ok_on_2xx():
    class FakeResponse:
        status_code = 200
    with patch("app.services.health.requests.request",
               return_value=FakeResponse()):
        p = health._probe_http("x", "X", "http://x.invalid/")
    assert p["status"] == "ok"
    assert "200" in p["detail"]


def test_http_probe_warn_on_4xx():
    class FakeResponse:
        status_code = 403
    with patch("app.services.health.requests.request",
               return_value=FakeResponse()):
        p = health._probe_http("x", "X", "http://x.invalid/")
    assert p["status"] == "warn"


def test_http_probe_error_on_5xx():
    class FakeResponse:
        status_code = 503
    with patch("app.services.health.requests.request",
               return_value=FakeResponse()):
        p = health._probe_http("x", "X", "http://x.invalid/")
    assert p["status"] == "error"


def test_http_probe_error_on_timeout():
    with patch("app.services.health.requests.request",
               side_effect=requests.exceptions.Timeout):
        p = health._probe_http("x", "X", "http://x.invalid/")
    assert p["status"] == "error"
    assert p["detail"] == "timeout"


def test_http_probe_error_on_connection_refused():
    with patch("app.services.health.requests.request",
               side_effect=requests.exceptions.ConnectionError):
        p = health._probe_http("x", "X", "http://x.invalid/")
    assert p["status"] == "error"
    assert "refused" in p["detail"]


def test_http_probe_never_leaks_url_on_failure():
    """A misconfigured URL with credentials in the query string mustn't
    end up in the detail string."""
    with patch("app.services.health.requests.request",
               side_effect=requests.exceptions.ConnectionError):
        p = health._probe_http("x", "X",
                               "http://x.invalid/?token=secret-leak")
    assert "secret-leak" not in p["detail"]


# ── Vault probe ──────────────────────────────────────────────────────────


def test_vault_probe_warn_when_file_missing(tmp_path, monkeypatch):
    missing = tmp_path / "nope.session"
    monkeypatch.setenv("VAULT_SESSION_FILE", str(missing))
    p = health._probe_vault_session()
    assert p["status"] == "warn"
    assert "missing" in p["detail"]


def test_vault_probe_ok_when_file_has_content(tmp_path, monkeypatch):
    f = tmp_path / "session"
    f.write_bytes(b"some-vault-token-bytes")
    monkeypatch.setenv("VAULT_SESSION_FILE", str(f))
    p = health._probe_vault_session()
    assert p["status"] == "ok"


def test_vault_probe_warn_when_file_empty(tmp_path, monkeypatch):
    f = tmp_path / "session"
    f.write_bytes(b"")
    monkeypatch.setenv("VAULT_SESSION_FILE", str(f))
    p = health._probe_vault_session()
    assert p["status"] == "warn"


# ── current_state + probe_all ────────────────────────────────────────────


def test_probe_all_populates_state(monkeypatch):
    """probe_all with no env vars + no vault file should still produce
    a deterministic snapshot."""
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_HEALTH_URL", raising=False)
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    monkeypatch.delenv("MINIO_HEALTH_URL", raising=False)
    monkeypatch.delenv("RADICALE_URL", raising=False)
    monkeypatch.delenv("RADICALE_HEALTH_URL", raising=False)
    monkeypatch.setenv("VAULT_SESSION_FILE", "/nonexistent/path")
    snap = health.probe_all()
    assert "components" in snap
    assert "overall" in snap
    assert "checked_at" in snap
    state = health.current_state()
    assert state["components"]
    # 4 probes
    assert len(state["components"]) == 4


def test_current_state_returns_copy():
    """Mutating the returned list must not affect the cache."""
    with health._PROBE_LOCK:
        health._PROBE_STATE["components"] = [{"status": "ok"}]
    state = health.current_state()
    state["components"].append({"status": "error"})
    with health._PROBE_LOCK:
        assert len(health._PROBE_STATE["components"]) == 1


# ── HTTP endpoint ────────────────────────────────────────────────────────


def test_pill_endpoint_requires_auth(client):
    r = client.get("/api/v1/health/pill")
    assert r.status_code == 401


def test_pill_endpoint_returns_cached_snapshot(auth_client):
    """Endpoint reads the module dict — never blocks on a network call."""
    with health._PROBE_LOCK:
        health._PROBE_STATE["components"] = [
            {"name": "x", "label": "X", "status": "ok",
             "detail": "all good", "checked_at": "2026-05-20T07:00:00+00:00"},
        ]
        health._PROBE_STATE["overall"] = "ok"
        health._PROBE_STATE["checked_at"] = "2026-05-20T07:00:00+00:00"

    r = auth_client.get("/api/v1/health/pill")
    assert r.status_code == 200
    body = r.get_json()
    assert body["overall"] == "ok"
    assert len(body["components"]) == 1
    assert body["components"][0]["name"] == "x"


# ── Pytest guard ─────────────────────────────────────────────────────────


def test_probe_thread_not_started_under_pytest():
    """create_app must not spawn a probe thread when PYTEST_CURRENT_TEST
    is set. The fixture system sets this for every test run."""
    assert health._PROBE_THREAD is None or not health._PROBE_THREAD.is_alive()
