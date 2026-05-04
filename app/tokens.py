"""Scoped API tokens.

Three scopes:
- `triage` — used by /api/v1/triage and /api/v1/<table>/<id>/confirm
- `bot`    — used by Telegram bot (REST client)
- `inbox`  — used by /api/v1/inbox capture endpoint (any source: bots,
             paperless bridge, voice memos, etc.)

Each scope has its own env var (TASKTRACK_TOKEN_TRIAGE,
TASKTRACK_TOKEN_BOT, TASKTRACK_TOKEN_INBOX). For backward
compatibility, a legacy single-secret TASKTRACK_TOKEN is accepted
across all scopes; using it logs a deprecation warning so we can
remove it once the systemd unit + vault item have been updated.

Routes call `check_scoped_token(scope)` from inside a request context;
it returns None on success or a (response, status) tuple on failure.
"""
import logging
import os

from flask import g, jsonify, request

LOG = logging.getLogger("tasktrack.tokens")

LEGACY_TOKEN = os.environ.get("TASKTRACK_TOKEN", "")
SCOPED_TOKENS = {
    "triage": os.environ.get("TASKTRACK_TOKEN_TRIAGE", ""),
    "bot": os.environ.get("TASKTRACK_TOKEN_BOT", ""),
    "inbox": os.environ.get("TASKTRACK_TOKEN_INBOX", ""),
}


def _presented_token() -> str:
    return (
        request.headers.get("X-Token")
        or request.headers.get("Authorization", "").replace("Bearer ", "")
    ).strip()


def check_scoped_token(scope: str):
    """Return None on valid, or a Flask response tuple on rejection."""
    if scope not in SCOPED_TOKENS:
        raise ValueError(f"unknown token scope: {scope!r}")

    presented = _presented_token()
    if not presented:
        return jsonify({
            "error": "unauthorized",
            "request_id": g.get("request_id", "-"),
        }), 401

    expected = SCOPED_TOKENS[scope]
    if expected and presented == expected:
        return None

    # Backward compat: the legacy single secret still works across all
    # scopes for one release cycle. We log it (not the token value) so
    # operators can spot which clients still need updating.
    if LEGACY_TOKEN and presented == LEGACY_TOKEN:
        LOG.warning(
            "legacy TASKTRACK_TOKEN used for scope=%s path=%s — "
            "rotate to scope-specific token",
            scope, request.path,
        )
        return None

    if not expected and not LEGACY_TOKEN:
        return jsonify({
            "error": "server token not configured",
            "request_id": g.get("request_id", "-"),
        }), 503

    return jsonify({
        "error": "unauthorized",
        "request_id": g.get("request_id", "-"),
    }), 401
