"""
The operational store.

Holds everything that is not knowledge: conversations waiting to be processed,
pipeline run history, items awaiting the user's decision, settings, and the
record of erasures.
"""

from lumen.operational.repositories import (
    DataErasureAuditRepository,
    HitlQueueRepository,
    IllegalStateTransitionError,
    OperationalError,
    OperationalStore,
    PipelineJobRepository,
    RecordNotFoundError,
    SessionBufferRepository,
    UnknownSettingKeyError,
    UserSettingsRepository,
)
from lumen.operational.sqlalchemy_impl import (
    KNOWN_SETTING_KEYS,
    SQLAlchemyOperationalStore,
    build_operational_store,
)

__all__ = [
    "OperationalStore",
    "SessionBufferRepository",
    "PipelineJobRepository",
    "HitlQueueRepository",
    "UserSettingsRepository",
    "DataErasureAuditRepository",
    "OperationalError",
    "RecordNotFoundError",
    "IllegalStateTransitionError",
    "UnknownSettingKeyError",
    "SQLAlchemyOperationalStore",
    "build_operational_store",
    "KNOWN_SETTING_KEYS",
]
