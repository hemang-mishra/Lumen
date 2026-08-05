"""
Database connection setup.

Builds the engine and hands out sessions. Also applies the SQLite settings that
have to be turned on by hand — most importantly foreign keys, which SQLite
ignores entirely unless asked not to.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from lumen.config import OperationalConfig

logger = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _apply_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """
    Turn on the SQLite settings the database needs to behave correctly.

    Runs for every new connection, and does nothing at all on other databases.

      foreign_keys — off by default in SQLite, which means every foreign key
                     and cascade would be silently ignored.
      journal_mode — write-ahead logging, so the API can read while a worker
                     is writing instead of blocking on it.
      busy_timeout — wait briefly for a lock rather than failing immediately.
    """
    if type(dbapi_connection).__module__.split(".")[0] != "sqlite3":
        return

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_ops_engine(config: OperationalConfig | None = None) -> Engine:
    """
    Build the database engine from configuration.

    In-memory SQLite gets special treatment: normally every connection would
    see its own empty database, so the pool is pinned to a single shared
    connection to keep one database alive for the whole test.
    """
    settings = config or OperationalConfig()
    url = settings.db_url
    kwargs: dict = {"echo": settings.echo_sql, "future": True}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool

    logger.debug("creating operational engine", extra={"db_url": _redact(url)})
    return create_engine(url, **kwargs)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """
    Build the factory that produces database sessions.

    expire_on_commit is off so that objects stay readable after a commit,
    which keeps callers from tripping over surprise reloads.
    """
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Generator[Session]:
    """
    Run a block of work inside one transaction.

    Commits if the block finishes, rolls back if it raises, and closes the
    session either way. Callers never have to remember to do any of that.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _redact(url: str) -> str:
    """Strip any password out of a connection URL before it reaches a log."""
    if "@" not in url or "//" not in url:
        return url
    scheme, _, rest = url.partition("//")
    credentials, _, host = rest.rpartition("@")
    if not credentials:
        return url
    user = credentials.split(":")[0]
    return f"{scheme}//{user}:***@{host}"


__all__ = [
    "create_ops_engine",
    "create_session_factory",
    "session_scope",
]
