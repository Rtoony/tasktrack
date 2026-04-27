"""Flask CLI commands.

Currently just `flask init-db`; Phase 1D replaces this with `flask db
upgrade` once Alembic owns schema management.
"""
import click
from flask import current_app
from flask.cli import with_appcontext

from .db import DB_PATH, init_db


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Initialize / migrate the SQLite schema. Safe to run on existing DB."""
    init_db(current_app.config.get("DB_PATH", DB_PATH))
    click.echo("init-db: ok")
