"""
Migration entry point.

Alembic loads this to work out which database to change and what the tables
are supposed to look like. The connection URL comes from application
configuration rather than from alembic.ini, so migrations follow the same
environment variables as the running application and cannot drift onto a
different database by accident.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from lumen.config import OperationalConfig
from lumen.operational.models import Base

config = context.config

# What the tables should look like. Alembic compares this against the real
# database to detect anything the migrations have not accounted for.
target_metadata = Base.metadata


def _database_url() -> str:
    """
    The database to migrate.

    A URL passed on the command line wins, so a one-off migration against some
    other database stays possible. Otherwise configuration decides.
    """
    return config.get_main_option("sqlalchemy.url") or OperationalConfig().db_url


def run_migrations_offline() -> None:
    """Emit the SQL without connecting, for review or manual application."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot alter a column in place. Batch mode rebuilds the table
        # instead, which is what makes future changes possible at all.
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply the migrations."""
    existing_connection = config.attributes.get("connection", None)

    if existing_connection is not None:
        # A connection supplied by the caller, which is how tests run
        # migrations against a temporary database.
        _run(existing_connection)
        return

    section = dict(config.get_section(config.config_ini_section) or {})
    section["sqlalchemy.url"] = _database_url()

    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)
    engine.dispose()


def _run(connection) -> None:
    """Apply migrations over an open connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
