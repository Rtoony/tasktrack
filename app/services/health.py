"""Pipeline status probes (Phase 5).

Runs lightweight checks against integrated Nexus services on a daemon
thread and exposes the latest snapshot via a shared in-process dict.
The HTTP endpoint reads that dict — it never makes a network call
itself — so a slow upstream can never block a page render.

Each probe returns a dict shape:
    {
        "name": "litellm",
        "label": "LiteLLM",
        "status": "ok" | "warn" | "error" | "n/a",
        "detail": "HTTP 200 in 12ms" | "connection refused" | "url not set",
        "checked_at": "2026-05-20T07:42:00+00:00",
    }

The aggregator returns the worst-of-all status (ignoring `n/a` so an
optional probe with no URL configured doesn't drag overall to error).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime

import requests

LOG = logging.getLogger("tasktrack.health")

# Severity ranking — used to pick the "overall" status.
_SEVERITY = {"ok": 0, "n/a": 0, "warn": 1, "error": 2}

# Cache of the latest probe results. Module-level so the route can read
# without going to the network. Lock-guarded because the probe thread
# writes here while the route reads.
_PROBE_STATE: dict[str, object] = {
    "components": [],
    "overall": "n/a",
    "checked_at": None,
}
_PROBE_LOCK = threading.Lock()
_PROBE_THREAD: threading.Thread | None = None
_PROBE_STOP = threading.Event()

PROBE_INTERVAL_SECONDS = 30
PROBE_TIMEOUT_SECONDS = 1.5


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _probe_http(name: str, label: str, url: str, method: str = "GET") -> dict:
    """Generic HTTP probe — 200/2xx = ok, anything else = warn/error."""
    if not url:
        return {"name": name, "label": label, "status": "n/a",
                "detail": "url not configured", "checked_at": _now_iso()}
    started = time.monotonic()
    try:
        res = requests.request(method, url, timeout=PROBE_TIMEOUT_SECONDS,
                               allow_redirects=False)
    except requests.exceptions.Timeout:
        return {"name": name, "label": label, "status": "error",
                "detail": "timeout", "checked_at": _now_iso()}
    except requests.exceptions.ConnectionError:
        return {"name": name, "label": label, "status": "error",
                "detail": "connection refused", "checked_at": _now_iso()}
    except requests.exceptions.RequestException as e:
        # Never log the full URL on failure — may carry credentials in a
        # query string. The exception class name is enough signal.
        return {"name": name, "label": label, "status": "error",
                "detail": e.__class__.__name__, "checked_at": _now_iso()}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    status_code = res.status_code
    if 200 <= status_code < 400:
        return {"name": name, "label": label, "status": "ok",
                "detail": f"HTTP {status_code} in {elapsed_ms}ms",
                "checked_at": _now_iso()}
    severity = "warn" if status_code < 500 else "error"
    return {"name": name, "label": label, "status": severity,
            "detail": f"HTTP {status_code} in {elapsed_ms}ms",
            "checked_at": _now_iso()}


def _probe_vault_session() -> dict:
    """Vault session file presence + readability (RAM-only)."""
    path = os.environ.get("VAULT_SESSION_FILE", "/dev/shm/nexus_session")
    label = "Vault session"
    if not os.path.exists(path):
        return {"name": "vault_session", "label": label, "status": "warn",
                "detail": f"missing {path}", "checked_at": _now_iso()}
    try:
        with open(path, "rb") as fh:
            data = fh.read(8)
    except OSError as e:
        return {"name": "vault_session", "label": label, "status": "error",
                "detail": f"unreadable: {e.__class__.__name__}",
                "checked_at": _now_iso()}
    return {"name": "vault_session", "label": label,
            "status": "ok" if data else "warn",
            "detail": "present" if data else "empty",
            "checked_at": _now_iso()}


def _build_probe_list() -> list[dict]:
    """Run every probe sequentially. Total wall-time bounded by the
    timeout × probe count. With 4 probes at 1.5s each, worst case is
    ~6s — still well under the 30s refresh interval."""
    litellm_url = (os.environ.get("LITELLM_HEALTH_URL")
                   or os.environ.get("LITELLM_BASE_URL", "")).rstrip("/")
    if litellm_url and not litellm_url.endswith("/health"):
        litellm_url = litellm_url + "/health"

    minio_url = os.environ.get("MINIO_HEALTH_URL", "")
    if not minio_url:
        endpoint = os.environ.get("MINIO_ENDPOINT", "").rstrip("/")
        if endpoint:
            minio_url = endpoint + "/minio/health/live"


    return [
        _probe_http("litellm", "LiteLLM", litellm_url),
        _probe_vault_session(),
        _probe_http("minio", "MinIO", minio_url, method="HEAD"),
    ]


def _aggregate(components: list[dict]) -> str:
    """worst-of-all severity, ignoring n/a entries."""
    worst = "ok"
    for c in components:
        s = c.get("status", "n/a")
        if s == "n/a":
            continue
        if _SEVERITY.get(s, 0) > _SEVERITY[worst]:
            worst = s
    return worst


def probe_all() -> dict:
    """Run probes and update the shared state. Returns the new snapshot."""
    components = _build_probe_list()
    overall = _aggregate(components)
    snapshot = {
        "components": components,
        "overall": overall,
        "checked_at": _now_iso(),
    }
    with _PROBE_LOCK:
        _PROBE_STATE.update(snapshot)
    return snapshot


def current_state() -> dict:
    """Return a defensive copy of the latest snapshot for the HTTP route."""
    with _PROBE_LOCK:
        return {
            "overall": _PROBE_STATE.get("overall", "n/a"),
            "checked_at": _PROBE_STATE.get("checked_at"),
            # Shallow copy of the component list — the dicts inside are
            # already immutable-by-convention (we replace, not mutate).
            "components": list(_PROBE_STATE.get("components", [])),
        }


def _probe_loop() -> None:
    """Daemon thread body. Stops when _PROBE_STOP is set so tests can
    deterministically wind down the thread if they need to."""
    while not _PROBE_STOP.is_set():
        try:
            probe_all()
        except Exception:
            LOG.exception("probe loop iteration failed")
        # Sleep in short chunks so a stop signal is honoured promptly.
        for _ in range(PROBE_INTERVAL_SECONDS):
            if _PROBE_STOP.is_set():
                return
            time.sleep(1)


def start_background_probes() -> None:
    """Spawn the daemon thread once per process. Safe to call repeatedly
    — second + calls are no-ops. Tests skip this via TESTING=True."""
    global _PROBE_THREAD
    if _PROBE_THREAD is not None and _PROBE_THREAD.is_alive():
        return
    _PROBE_STOP.clear()
    # Prime the dict synchronously so the first request after boot sees
    # real data instead of the empty default.
    try:
        probe_all()
    except Exception:
        LOG.exception("initial probe failed")
    _PROBE_THREAD = threading.Thread(
        target=_probe_loop, name="tasktrack-probe", daemon=True,
    )
    _PROBE_THREAD.start()
    LOG.info("health probe thread started (interval=%ds)",
             PROBE_INTERVAL_SECONDS)


def stop_background_probes() -> None:
    """Signal the probe thread to exit. Used in tests; production never
    calls this — daemon=True lets the interpreter teardown handle it."""
    _PROBE_STOP.set()
