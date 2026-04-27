"""Gunicorn configuration.

Replaces the inline ExecStart args in collab-tracker.service. Bind host
comes from the deployment profile (`personal` → 0.0.0.0; `company` →
127.0.0.1 with cloudflared proxying).
"""
import os

# Bind comes from profile/env so a company-profile install isn't
# accidentally exposed on a public interface.
_bind_host = os.environ.get("BIND_HOST") or "0.0.0.0"
_bind_port = os.environ.get("BIND_PORT", "5050")
bind = f"{_bind_host}:{_bind_port}"

workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "sync"

# Triage can take ~90s when AI Intake is enabled (LiteLLM call). The
# request timeout has to clear that. Phase 8 background-job queue moves
# AI calls async; this can drop back to 30 then.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))

# We emit our own structured access log via app.middleware (every line
# carries the request ID), so gunicorn's built-in access log is silenced
# to avoid double entries. Errors still go to stderr / journal.
accesslog = None
errorlog = "-"
loglevel = "warning"
