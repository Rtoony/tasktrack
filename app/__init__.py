"""TaskTrack — internal operations tracker (app factory).

create_app() builds a fresh Flask instance, registers all blueprints,
wires the SQLite teardown, configures logging, the request-ID
middleware, error handlers, and the intake-form rate limiter, and
adds the `flask init-db` CLI command.

Module-level re-exports preserve the legacy import surface:
  from app import ALLOWED_TABLES, DB_PATH, validate_record_data
which telegram_bot.py and email_intake.py rely on.
"""
import logging
import os

from flask import Flask, g, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import create_engine, inspect
from werkzeug.exceptions import HTTPException

from . import profile as _profile
from .config import (
    ADMIN_WORKFLOW_VIEWS,
    ALLOWED_TABLES,
    BRIDGE_MAP,
    SIMPLE_SUBMISSION_CONFIGS,
)
from .csrf import init_csrf
from .db import DB_PATH, close_session, get_secret_key
from .logging_config import configure_logging
from .middleware import init_request_middleware
from .models import Base
from .services.tickets import validate_record_data

LOG = logging.getLogger("tasktrack.app")

__all__ = [
    "create_app",
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
    """Build a configured Flask app instance.

    DB path resolution order:
      1. `db_path` argument (used by tests and internal callers).
      2. `DB_PATH` environment variable.
      3. The module-level DB_PATH constant (project-root tracker.db).
    """
    configure_logging(log_format=_profile.LOG_FORMAT)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, "templates"),
        static_folder=os.path.join(project_root, "static"),
        static_url_path="/static",
    )
    app.config["DB_PATH"] = db_path or os.environ.get("DB_PATH") or DB_PATH
    app.secret_key = get_secret_key(app.config["DB_PATH"])
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = _profile.SESSION_COOKIE_SECURE
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    # Hard upload cap (Werkzeug rejects multipart bodies past this with
    # a 413 before any request handler runs). Service layer enforces the
    # same limit again while streaming, so this is only the outer gate.
    try:
        app.config["MAX_CONTENT_LENGTH"] = int(
            os.environ.get("ATTACHMENT_MAX_BYTES", str(50 * 1024 * 1024))
        )
    except ValueError:
        app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    for key, value in _profile.summary().items():
        app.config[key] = value

    LOG.info("starting brand=%r log=%s bind=%s",
             _profile.BRAND_NAME, _profile.LOG_FORMAT, _profile.BIND_HOST)

    _check_schema_matches_models(app.config["DB_PATH"])
    _check_bridge_map_fields()

    app.teardown_appcontext(close_session)
    init_request_middleware(app)
    init_csrf(app)
    limiter.init_app(app)
    _register_error_handlers(app)
    _register_legacy_api_redirect(app)
    _register_json_gzip(app)

    @app.context_processor
    def _inject_template_context():
        return {
            "brand_name": _profile.BRAND_NAME,
            "attachment_max_bytes": app.config["MAX_CONTENT_LENGTH"],
        }

    from .routes.admin import bp as admin_bp
    from .routes.api import bp as api_bp
    from .routes.attachments import bp as attachments_bp
    from .routes.auth import bp as auth_bp
    from .routes.bridges import bp as bridges_bp
    from .routes.competency import bp as competency_bp
    from .routes.health_pill import bp as health_pill_bp
    from .routes.inbox import bp as inbox_bp
    from .routes.intake import bp as intake_bp
    from .routes.links import bp as links_bp
    from .routes.main import bp as main_bp
    from .routes.registry import bp as registry_bp
    from .routes.telegram_api import bp as telegram_api_bp
    from .routes.triage import bp as triage_bp
    from .routes.weekly import bp as weekly_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(intake_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(telegram_api_bp)
    app.register_blueprint(attachments_bp)
    app.register_blueprint(links_bp)
    app.register_blueprint(inbox_bp)
    app.register_blueprint(triage_bp)
    app.register_blueprint(registry_bp)
    app.register_blueprint(competency_bp)
    app.register_blueprint(bridges_bp)
    app.register_blueprint(health_pill_bp)
    app.register_blueprint(weekly_bp)

    # Phase-5: background health probes. Skipped under pytest so we don't
    # spawn a thread per test fixture. The conftest sets TESTING after
    # create_app returns, so checking config here is too early; use the
    # PYTEST_CURRENT_TEST env var instead, which pytest sets for every
    # test run.
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        from .services.health import start_background_probes
        start_background_probes()

    from .cli import create_admin_command, db_upgrade_command, init_db_command
    app.cli.add_command(init_db_command)
    app.cli.add_command(db_upgrade_command)
    app.cli.add_command(create_admin_command)

    return app


def _check_schema_matches_models(db_path: str) -> None:
    """Refuse to start if the live SQLite has drifted from the model definitions.

    The schema is owned by Alembic; this guard catches the case where
    the DB has been mutated outside of Alembic since the baseline was
    stamped. Permissive about EXTRA columns the live DB has but strict
    about MISSING columns and tables that the models expect.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    insp = inspect(engine)
    live_tables = set(insp.get_table_names())
    issues = []
    for table in Base.metadata.tables.values():
        if table.name not in live_tables:
            issues.append(f"table missing: {table.name}")
            continue
        live_cols = {c["name"] for c in insp.get_columns(table.name)}
        model_cols = {c.name for c in table.columns}
        missing = model_cols - live_cols
        if missing:
            issues.append(f"{table.name}: missing columns {sorted(missing)}")
    engine.dispose()
    if issues:
        raise RuntimeError(
            "schema drift detected — refusing to start. "
            "Run `alembic upgrade head` or repair the DB. Issues: "
            + "; ".join(issues)
        )
    LOG.info("schema check: %d tables match models", len(Base.metadata.tables))


def _check_bridge_map_fields() -> None:
    """Refuse to start if BRIDGE_MAP references unknown tables or fields.

    Same fail-loud pattern as _check_schema_matches_models. Catches typos
    in the carry dict, defaults dict, or title_field before they become
    silent dropped-fields at runtime."""
    issues = []
    for src_table, targets in BRIDGE_MAP.items():
        if src_table not in ALLOWED_TABLES:
            issues.append(f"unknown source table: {src_table!r}")
            continue
        for tgt_table, rule in targets.items():
            if tgt_table not in ALLOWED_TABLES:
                issues.append(
                    f"bridge {src_table}->{tgt_table}: unknown target table"
                )
                continue
            tgt_fields = set(ALLOWED_TABLES[tgt_table]["fields"])
            src_fields = set(ALLOWED_TABLES[src_table]["fields"])
            for sf, tf in rule.get("carry", {}).items():
                if sf not in src_fields:
                    issues.append(
                        f"bridge {src_table}->{tgt_table}: source field "
                        f"{sf!r} not in ALLOWED_TABLES[{src_table!r}]"
                    )
                if tf not in tgt_fields:
                    issues.append(
                        f"bridge {src_table}->{tgt_table}: target field "
                        f"{tf!r} not in ALLOWED_TABLES[{tgt_table!r}]"
                    )
            for df in rule.get("defaults", {}):
                if df not in tgt_fields:
                    issues.append(
                        f"bridge {src_table}->{tgt_table}: defaults key "
                        f"{df!r} not in ALLOWED_TABLES[{tgt_table!r}]"
                    )
            tf = rule.get("title_field")
            if tf and tf not in tgt_fields:
                issues.append(
                    f"bridge {src_table}->{tgt_table}: title_field "
                    f"{tf!r} not in ALLOWED_TABLES[{tgt_table!r}]"
                )
    if issues:
        raise RuntimeError(
            "BRIDGE_MAP drift detected — refusing to start. Issues: "
            + "; ".join(issues)
        )
    n_pairs = sum(len(t) for t in BRIDGE_MAP.values())
    LOG.info("bridge check: %d pairs OK", n_pairs)


def _register_legacy_api_redirect(app: Flask) -> None:
    """308-redirect /api/<rest> -> /api/v1/<rest> for one release cycle.

    The 308 status preserves the request method (so POST/PUT/DELETE
    aren't downgraded to GET) and tells caches the move is permanent.
    """
    from flask import redirect

    @app.before_request
    def _legacy_api_to_v1():
        path = request.path
        if path.startswith("/api/") and not path.startswith("/api/v1/"):
            new_path = "/api/v1" + path[len("/api"):]
            if request.query_string:
                new_path += "?" + request.query_string.decode("ascii", errors="ignore")
            return redirect(new_path, code=308)

    @app.before_request
    def _legacy_submit_to_intake():
        # /submit/* -> /intake/*. Capability is intentionally not redirected:
        # /submit/capability returns 404 (intake surface removed).
        path = request.path
        if path == "/submit/capability" or path == "/intake/capability":
            return jsonify({
                "error": "capability submissions are not part of the intake surface",
                "request_id": g.get("request_id", "-"),
            }), 404
        if path == "/submit" or path.startswith("/submit/"):
            new_path = "/intake" + path[len("/submit"):]
            if request.query_string:
                new_path += "?" + request.query_string.decode("ascii", errors="ignore")
            return redirect(new_path, code=308)


def _register_json_gzip(app: Flask) -> None:
    """gzip JSON responses larger than ~1 KB when the client accepts it.

    Saves ~85% on the projects geojson endpoint (1.9 MB -> ~280 KB).
    Skips: non-JSON responses, already-encoded responses, small payloads,
    and clients that don't advertise gzip support.
    """
    import gzip

    MIN_BYTES = 1024

    @app.after_request
    def _maybe_gzip(response):
        if response.direct_passthrough:
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if response.headers.get("Content-Encoding"):
            return response
        if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
            return response
        ctype = (response.headers.get("Content-Type") or "").lower()
        if not ctype.startswith("application/json"):
            return response
        data = response.get_data()
        if len(data) < MIN_BYTES:
            return response
        response.set_data(gzip.compress(data, compresslevel=6))
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(response.get_data()))
        vary = response.headers.get("Vary")
        response.headers["Vary"] = (
            "Accept-Encoding" if not vary
            else (vary if "accept-encoding" in vary.lower() else f"{vary}, Accept-Encoding")
        )
        return response


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
