"""Lightweight CSRF protection.

Per-session token stored in the Flask session. Templates inject it
via the `csrf_token` context variable into a hidden form field. State-
changing requests (POST/PUT/PATCH/DELETE) must echo the token back
either as a form field named `csrf_token` or as the `X-CSRF-Token`
header (used by fetch/XHR calls in templates).

Exemptions:
- Endpoints under /api/v1/* that authenticate with a scoped bearer
  token (X-Token / Authorization) are exempt — they are not cookie-
  driven sessions and so are not vulnerable to CSRF.
- The webhook-style /api/v1/triage, /api/v1/inbox/*, /api/v1/telegram/*
  routes are token-authenticated and are covered by the exemption above.

We deliberately keep this in ~50 lines rather than pulling in Flask-WTF
to avoid the dependency footprint for a single-user tool.
"""
import logging
import secrets as _secrets

from flask import g, jsonify, request, session

LOG = logging.getLogger("tasktrack.csrf")

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_SESSION_KEY = "_csrf_token"


def get_csrf_token() -> str:
    """Return (or mint) the per-session CSRF token."""
    token = session.get(_SESSION_KEY)
    if not token:
        token = _secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def _request_has_bearer_token() -> bool:
    """True if the request authenticates with a scoped bearer token.

    These requests are NOT cookie-driven, so CSRF doesn't apply.
    """
    if request.headers.get("X-Token"):
        return True
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ")


def init_csrf(app):
    """Wire CSRF validation as a before_request hook + template helper."""

    @app.before_request
    def _csrf_protect():
        if request.method in _SAFE_METHODS:
            return None
        # Pytest fixtures use the Flask test client without CSRF flow.
        if app.config.get("TESTING"):
            return None
        # Token-authenticated API calls (bot, paperless bridge, voice memos,
        # triage clients) are exempt — they don't use session cookies.
        if _request_has_bearer_token():
            return None

        expected = session.get(_SESSION_KEY, "")
        presented = (
            request.headers.get("X-CSRF-Token", "")
            or request.form.get("csrf_token", "")
        )
        if expected and presented and _secrets.compare_digest(presented, expected):
            return None

        LOG.warning(
            "csrf reject path=%s method=%s rid=%s",
            request.path, request.method, g.get("request_id", "-"),
        )
        if request.path.startswith("/api/"):
            return jsonify({
                "error": "csrf token missing or invalid",
                "request_id": g.get("request_id", "-"),
            }), 403
        return ("CSRF token missing or invalid", 403)

    @app.context_processor
    def _inject_csrf():
        return {"csrf_token": get_csrf_token()}
