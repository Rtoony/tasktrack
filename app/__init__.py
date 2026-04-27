"""TaskTrack — collaborative task tracker (app factory).

create_app() builds a fresh Flask instance, registers blueprints
(some of them gated by deployment-profile feature flags), wires the
SQLite teardown, and adds the `flask init-db` CLI command.

Module-level re-exports preserve the legacy import surface:
  from app import ALLOWED_TABLES, DB_PATH, validate_record_data
which telegram_bot.py and email_intake.py rely on. Phase 1C will
replace those imports with REST calls and let us drop the re-exports.
"""
import logging
import os

from flask import Flask

from . import profile as _profile
from .config import ADMIN_WORKFLOW_VIEWS, ALLOWED_TABLES, SIMPLE_SUBMISSION_CONFIGS
from .db import DB_PATH, close_db, get_secret_key, init_db
from .services.tickets import validate_record_data

LOG = logging.getLogger("tasktrack.app")

__all__ = [
    "create_app",
    "init_db",
    # legacy import surface for telegram_bot.py / email_intake.py
    "ALLOWED_TABLES",
    "ADMIN_WORKFLOW_VIEWS",
    "SIMPLE_SUBMISSION_CONFIGS",
    "DB_PATH",
    "validate_record_data",
]


def create_app(db_path=None) -> Flask:
    """Build a configured Flask app instance."""
    # Templates live at the project root, not inside the app/ package —
    # point Flask at them explicitly. There's no static/ today; the
    # default /static endpoint will 404 if hit (matches prior behavior).
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

    LOG.info("starting profile=%s ai_intake=%s calendar=%s brand=%r",
             _profile.PROFILE, _profile.ENABLE_AI_INTAKE,
             _profile.ENABLE_CALENDAR_WIDGET, _profile.BRAND_NAME)
    for key, default, override in _profile.overrides():
        LOG.warning("profile=%s override: %s default=%r env=%r",
                    _profile.PROFILE, key, default, override)

    app.teardown_appcontext(close_db)

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
