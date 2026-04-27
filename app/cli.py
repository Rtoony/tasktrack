"""Flask CLI commands.

Schema is now managed by Alembic (`alembic upgrade head` or, equivalently,
`flask db upgrade`). The legacy `flask init-db` is preserved as a thin
shim that delegates to Alembic so existing deploy notes keep working.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from .db import DB_PATH

LOG = logging.getLogger("tasktrack.cli")


def _run_alembic_upgrade(db_path: str) -> int:
    """Invoke `alembic upgrade head` against the given DB path."""
    project_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["TASKTRACK_DATABASE_URL"] = f"sqlite:///{db_path}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, err=True, nl=False)
    return result.returncode


@click.command("db-upgrade")
@with_appcontext
def db_upgrade_command():
    """Run pending Alembic migrations against the live DB."""
    db_path = current_app.config.get("DB_PATH", DB_PATH)
    rc = _run_alembic_upgrade(db_path)
    if rc != 0:
        raise click.ClickException(f"alembic upgrade failed (rc={rc})")
    click.echo("db-upgrade: ok")


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Compatibility shim — delegates to `alembic upgrade head`.

    Replaces the legacy runtime schema mutator removed in Phase 1D-2j.
    """
    db_path = current_app.config.get("DB_PATH", DB_PATH)
    rc = _run_alembic_upgrade(db_path)
    if rc != 0:
        raise click.ClickException(f"alembic upgrade failed (rc={rc})")
    click.echo("init-db: ok (alembic upgrade head)")
