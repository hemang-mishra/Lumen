"""
Running migrations from Python.

The alembic command line is the normal way to work with migrations. These
helpers exist for the cases where that is awkward: application startup, and
tests that need a freshly migrated database of their own.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine

from lumen.operational.models import Base

logger = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_ROOT.parent.parent


def build_alembic_config(engine: Engine | None = None) -> Config:
    """
    Load the migration settings.

    When an engine is given, migrations run over that engine's connection
    instead of opening a second one. That matters for in-memory databases,
    where a separate connection would be a separate, empty database.
    """
    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PACKAGE_ROOT / "migrations"))

    if engine is not None:
        config.set_main_option("sqlalchemy.url", str(engine.url))
        config.attributes["connection"] = None

    return config


def upgrade_to_head(engine: Engine) -> None:
    """Bring a database up to the latest schema."""
    config = build_alembic_config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    logger.debug("operational schema upgraded to head")


def downgrade_to_base(engine: Engine) -> None:
    """Undo every migration, leaving the database empty."""
    config = build_alembic_config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")


def detect_schema_drift(engine: Engine) -> list:
    """
    Compare a migrated database against the table definitions in code.

    An empty result means the two agree. Anything else means a model was
    changed without a matching migration, which would otherwise only surface
    later as a confusing error against a real database.
    """
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "render_as_batch": True},
        )
        return compare_metadata(context, Base.metadata)


__all__ = [
    "build_alembic_config",
    "upgrade_to_head",
    "downgrade_to_base",
    "detect_schema_drift",
]
