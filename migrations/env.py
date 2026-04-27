"""Alembic environment.

DB URL is read from the TASKTRACK_DATABASE_URL env var, falling back
to alembic.ini, then to the live SQLite path. The TaskTrack-specific
name avoids colliding with any DATABASE_URL the shell may have set
for unrelated apps (e.g., rtoony-portal Postgres).
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the project root importable so we can `from app.models import Base`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve the DB URL: env override wins, then alembic.ini, then live default.
db_url = (
    os.environ.get("TASKTRACK_DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
    or f"sqlite:///{PROJECT_ROOT / 'tracker.db'}"
)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=db_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=db_url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
