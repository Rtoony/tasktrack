"""TaskTrack — collaborative task tracker (app factory).

create_app() builds a fresh Flask instance, registers blueprints
(some of them gated by deployment-profile feature flags), wires the
SQLite teardown, configures structured/text logging, the request-ID
middleware, error handlers, and the intake-form rate limiter, and
adds the `flask init-db` CLI command.

Module-level re-exports preserve the legacy import surface:
  from app import ALLOWED_TABLES, DB_PATH, validate_record_data
which telegram_bot.py and email_intake.py rely on. Phase 1C will
replace those imports with REST calls and let us drop the re-exports.
"""
import logging
import os

from flask import Flask, g, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException

from . import profile as _profile
from .config import ADMIN_WORKFLOW_VIEWS, ALLOWED_TABLES, SIMPLE_SUBMISSION_CONFIGS
from .db import DB_PATH, close_db, get_secret_key, init_db
from .logging_config import configure_logging
from .middleware import init_request_middleware
from .services.tickets import validate_record_data

LOG = logging.getLogger("tasktrack.app")

__all__ = [
    "create_app",
    "init_db",
    "limiter",
    # legacy import surface for telegram_bot.py / email_intake.py
    "ALLOWED_TABLES",
    "ADMIN_WORKFLOW_VIEWS",
    "SIMPLE_SUBMISSION_CONFIGS",
    "DB_PATH",
    "validate_record_data",
]

# Limiter is initialized at module scope so route decorators can
# reference it; init_app() is called inside create_app().
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],  # nothing global — we limit specific routes only
    storage_uri="memory://",
    headers_enabled=True,
)


def create_app(db_path=None) -> Flask:
    """Build a configured Flask app instance."""
    # Configure logging first so subsequent import-time logs go through
    # our formatter. Re-running it on each create_app() is idempotent
    # (dictConfig overwrites the root config).
    configure_logging(log_format=_profile.LOG_FORMAT)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, "templates"),
    )
    app.config["DB_PATH"] = db_path or DB_PATH
    app.secret_key = get_secret_key(app.config["DB_PATH"])
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    # Profile + feature flags into app.config so route handlers and
    # templates can read them via current_app.config[...].
    for key, value in _profile.summary().items():
        app.config[key] = value

    LOG.info("starting profile=%s ai_intake=%s calendar=%s brand=%r log=%s",
             _profile.PROFILE, _profile.ENABLE_AI_INTAKE,
             _profile.ENABLE_CALENDAR_WIDGET, _profile.BRAND_NAME,
             _profile.LOG_FORMAT)
    for key, default, override in _profile.overrides():
        LOG.warning("profile=%s override: %s default=%r env=%r",
                    _profile.PROFILE, key, default, override)

    app.teardown_appcontext(close_db)
    init_request_middleware(app)
    limiter.init_app(app)
    _register_error_handlers(app)

    # Inject feature flags + branding into every Jinja render so templates
    # can gate UI without route-by-route context plumbing.
    @app.context_processor
    def _inject_profile_context():
        return {
            "ai_intake_enabled": _profile.ENABLE_AI_INTAKE,
            "calendar_enabled": _profile.ENABLE_CALENDAR_WIDGET,
            "brand_name": _profile.BRAND_NAME,
            "tasktrack_profile": _profile.PROFILE,
        }

    # Always-on blueprints.
    from .routes.auth import bp as auth_bp
    from .routes.main import bp as main_bp
    from .routes.intake import bp as intake_bp
    from .routes.api import bp as api_bp
    from .routes.admin import bp as admin_bp
    from .routes.maximus import bp as maximus_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(intake_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(maximus_bp)

    # Feature-flagged blueprints. AI Intake is experimental and currently
    # not part of the company release (decision 2026-04-27); the calendar
    # widget is Nexus-specific Radicale integration that the company
    # product replaces with Outlook in Phase 8.
    if _profile.ENABLE_AI_INTAKE:
        from .routes.triage import bp as triage_bp
        app.register_blueprint(triage_bp)

    if _profile.ENABLE_CALENDAR_WIDGET:
        from .routes.calendar import bp as calendar_bp
        app.register_blueprint(calendar_bp)

    from .cli import init_db_command
    app.cli.add_command(init_db_command)

    return app


def _register_error_handlers(app: Flask) -> None:
    """Return JSON for /api/* errors with the request ID; HTML pass-through elsewhere."""

    @app.errorhandler(HTTPException)
    def _http_exc(e):
        rid = g.get("request_id", "-")
        if request.path.startswith("/api/"):
            return jsonify({
                "error": e.description or "error",
                "code": e.code,
                "request_id": rid,
            }), e.code
        return e

    @app.errorhandler(Exception)
    def _unhandled(e):
        rid = g.get("request_id", "-")
        # Always log non-HTTP exceptions with full traceback server-side.
        # Body is never logged. The client sees only the request ID.
        LOG.exception("unhandled exception path=%s rid=%s", request.path, rid)
        if request.path.startswith("/api/"):
            return jsonify({
                "error": "internal server error",
                "request_id": rid,
            }), 500
        return ("Internal Server Error — see logs for request id " + rid, 500)
