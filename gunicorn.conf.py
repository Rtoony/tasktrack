"""Gunicorn configuration."""
import os

# Bind to 0.0.0.0 so other devices on the LAN/Tailscale can reach the
# UI. Override with BIND_HOST=127.0.0.1 if a reverse proxy fronts it.
_bind_host = os.environ.get("BIND_HOST") or "0.0.0.0"
_bind_port = os.environ.get("BIND_PORT", "5050")
bind = f"{_bind_host}:{_bind_port}"

workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "sync"

# Triage can take ~90s when AI Intake actually fires (LiteLLM call).
# Keep the request timeout above that until AI moves to a background queue.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))

# We emit our own structured access log via app.middleware (every line
# carries the request ID), so gunicorn's built-in access log is silenced
# to avoid double entries. Errors still go to stderr / journal.
accesslog = None
errorlog = "-"
loglevel = "warning"
