"""Flask CLI commands.

Schema is now managed by Alembic (`alembic upgrade head` or, equivalently,
`flask db upgrade`). The legacy `flask init-db` is preserved as a thin
shim that delegates to Alembic so existing deploy notes keep working.

`flask create-admin` bootstraps the first admin user on a fresh deploy
without manual SQL.
"""
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from .db import DB_PATH, get_session
from .models import ApprovedEmail, User

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


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@click.command("create-admin")
@click.option("--email", required=True, help="Admin email address.")
@click.option("--name", required=True, help="Display name shown in the UI.")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Initial password (>= 6 chars).",
)
@with_appcontext
def create_admin_command(email: str, name: str, password: str):
    """Bootstrap an admin user on a fresh deploy.

    Idempotent: if the email already exists, the role is upgraded to
    'admin' and the password is reset. Adds the email to
    approved_emails so future self-registration would also work.
    """
    email = email.strip().lower()
    name = name.strip()
    if not _EMAIL_RE.match(email):
        raise click.ClickException(f"invalid email: {email!r}")
    if not name:
        raise click.ClickException("--name must not be empty")
    if len(password) < 6:
        raise click.ClickException("password must be at least 6 characters")

    sess = get_session()

    # 1. Make sure the email is on the approved list.
    if sess.scalar(select(ApprovedEmail).where(func.lower(ApprovedEmail.email) == email)) is None:
        sess.add(ApprovedEmail(email=email))

    # 2. Upsert the user with role=admin.
    user = sess.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        user = User(
            email=email,
            display_name=name,
            password_hash=generate_password_hash(password),
            role="admin",
        )
        sess.add(user)
        action = "created"
    else:
        user.display_name = name
        user.password_hash = generate_password_hash(password)
        user.role = "admin"
        action = "updated"

    sess.commit()
    click.echo(f"create-admin: {action} {email} (role=admin)")
