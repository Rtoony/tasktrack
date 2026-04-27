"""TaskTrack — collaborative task tracker (app factory).

create_app() builds a fresh Flask instance, registers all blueprints,
wires the SQLite teardown, and adds the `flask init-db` CLI command.

Module-level re-exports preserve the legacy import surface:
  from app import ALLOWED_TABLES, DB_PATH, validate_record_data
which telegram_bot.py and email_intake.py rely on. Phase 1C will
replace those imports with REST calls and let us drop the re-exports.
"""
import os

from flask import Flask

from .config import ADMIN_WORKFLOW_VIEWS, ALLOWED_TABLES, SIMPLE_SUBMISSION_CONFIGS
from .db import DB_PATH, close_db, get_secret_key, init_db
from .services.tickets import validate_record_data

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

    app.teardown_appcontext(close_db)

    # Register blueprints. Imports are deferred so the package can be
    # imported (for the legacy re-exports above) without pulling in
    # Flask-context-dependent modules.
    from .routes.auth import bp as auth_bp
    from .routes.main import bp as main_bp
    from .routes.intake import bp as intake_bp
    from .routes.api import bp as api_bp
    from .routes.admin import bp as admin_bp
    from .routes.triage import bp as triage_bp
    from .routes.maximus import bp as maximus_bp
    from .routes.calendar import bp as calendar_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(intake_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(triage_bp)
    app.register_blueprint(maximus_bp)
    app.register_blueprint(calendar_bp)

    from .cli import init_db_command
    app.cli.add_command(init_db_command)

    return app
