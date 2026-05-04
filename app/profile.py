"""Single-user TaskTrack settings.

This used to be a deployment-profile selector with `personal` and `company`
defaults. The company rollout was scrapped 2026-05-04 — TaskTrack is now a
single-user personal tool, and there is exactly one set of settings.

Everything is still env-overridable for the few cases that need it (e.g.
running tests with a tighter rate limit, or flipping SESSION_COOKIE_SECURE
to true behind an HTTPS proxy), but there is no longer a profile to choose.
"""
import logging
import os

LOG = logging.getLogger("tasktrack.profile")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        LOG.warning("invalid int for %s=%r; using default %d", name, raw, default)
        return default


def _str(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ── Branding ──────────────────────────────────────────────────────────────
BRAND_NAME = _str("BRAND_NAME", "TaskTrack")

# ── Bind / cookies ────────────────────────────────────────────────────────
BIND_HOST = _str("BIND_HOST", "0.0.0.0")
SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)

# ── Logging ───────────────────────────────────────────────────────────────
# "text" for tail-readable lines, "structured" for JSON shipping.
LOG_FORMAT = _str("LOG_FORMAT", "text")

# ── Intake forms ──────────────────────────────────────────────────────────
# Per-IP cap on POSTs to /intake/* — keeps a runaway script from drowning
# the form surface even on a personal install.
INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP = _int("INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP", 60)


def summary() -> dict:
    return {
        "BRAND_NAME": BRAND_NAME,
        "BIND_HOST": BIND_HOST,
        "SESSION_COOKIE_SECURE": SESSION_COOKIE_SECURE,
        "LOG_FORMAT": LOG_FORMAT,
        "INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP": INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP,
    }
