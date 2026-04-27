"""Request middleware: per-request IDs, access logging.

A request ID is set on every incoming request from `X-Request-Id` if the
client provided one, otherwise we generate a short hex token. It's
stored on `g.request_id`, echoed back on the response, and surfaced in
every log line via `RequestIdFilter`.
"""
import logging
import secrets
import time

from flask import g, request

ACCESS_LOG = logging.getLogger("tasktrack.access")
APP_LOG = logging.getLogger("tasktrack.app")


def init_request_middleware(app):
    """Wire before/after hooks for request IDs + access logging."""

    @app.before_request
    def _begin_request():
        incoming = request.headers.get("X-Request-Id", "")
        # Trust client-supplied IDs only if they look reasonable.
        if incoming and 4 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum():
            g.request_id = incoming
        else:
            g.request_id = secrets.token_hex(6)
        g.request_started_at = time.monotonic()

    @app.after_request
    def _end_request(response):
        rid = g.get("request_id", "-")
        response.headers["X-Request-Id"] = rid
        # Access log — never log request bodies (may contain confidential
        # project data). Only method, path, status, latency.
        elapsed_ms = int((time.monotonic() - g.get("request_started_at", time.monotonic())) * 1000)
        ACCESS_LOG.info(
            "%s %s -> %s %dms",
            request.method, request.path, response.status_code, elapsed_ms,
        )
        return response
