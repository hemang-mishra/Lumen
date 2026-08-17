"""
What a day's conversation remembers about itself.

A session here is one calendar day, not one chat window and not a stretch of
activity between pauses. That is a deliberate match to how the person
actually experiences it: sleeping resets the state they are in, and a new day
genuinely is a new context. Sending a message at 11:58 pm and another at
12:02 am puts the second one in tomorrow.

Nothing here is saved anywhere. Everything it holds is worth exactly one
day — the recent turns, and which sensitive areas of life the person has
opened up themselves. The durable record of the same conversation is kept on
the ingestion side, and that is the one that becomes graph history.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime

from lumen.config import QueryConfig
from lumen.query.buffer import SessionContextBuffer
from lumen.schemas.enums import Domain
from lumen.schemas.query import ChatTurn

logger = logging.getLogger(__name__)

DEFAULT_LABEL = ""


def make_session_id(user_id: str, event_date: date, label: str = DEFAULT_LABEL) -> str:
    """
    The identifier for one person's conversation on one day.

    Built the same way the stored conversations are keyed, so a live session
    and the buffer that will eventually be processed into graph history can
    be recognised as the same thing.
    """
    stamp = event_date.strftime("%Y_%m_%d")
    return f"{user_id}_{stamp}_{label}" if label else f"{user_id}_{stamp}"


@dataclass
class ChatSession:
    """
    One day of conversation, held in memory while it happens.

    Three things live here and they have different lifetimes in spirit. The
    turns are a short rolling window, kept only so a turn that makes no sense
    alone can be read against the ones before it. The unlocked areas are
    cumulative for the whole day: once somebody has raised a painful subject
    themselves, the most guarded records about it stay available until the
    day ends, and are locked again tomorrow. The context buffer is today's
    thread — the few records already surfaced that are still worth offering
    again — and it is emptied by the same midnight rule as everything else.

    The known era names are cached here rather than looked up per turn.
    They change only when the pipeline writes new history, which cannot
    happen in the middle of a live conversation.
    """

    session_id: str
    user_id: str
    event_date: date
    session_label: str = DEFAULT_LABEL
    created_at: datetime | None = None
    last_activity_at: datetime | None = None
    max_turns: int = 200
    context_buffer: SessionContextBuffer = field(
        default_factory=SessionContextBuffer, repr=False
    )

    _turns: deque[ChatTurn] = field(default_factory=deque, repr=False)
    _unlocked: set[Domain] = field(default_factory=set, repr=False)
    _era_vocabulary: tuple[str, ...] | None = field(default=None, repr=False)

    # How many turns have happened is something the session works out, so it
    # is never something a caller can state. Counted separately from the
    # stored turns because those are a window that drops its oldest, and the
    # numbering must not restart when it does.
    _counted: int = field(default=0, repr=False, init=False)

    def __post_init__(self) -> None:
        # deque enforces the ceiling itself, so a conversation that runs all
        # day drops its oldest turns instead of growing without limit.
        self._turns = deque(self._turns, maxlen=max(self.max_turns, 1))

    @property
    def turn_count(self) -> int:
        """How many turns have been recorded today."""
        return self._counted

    @property
    def unlocked_domains(self) -> tuple[Domain, ...]:
        """Areas of life the person has opened themselves today, in a stable order."""
        return tuple(sorted(self._unlocked, key=lambda domain: domain.value))

    @property
    def era_vocabulary(self) -> tuple[str, ...] | None:
        """The era names this graph holds, or None if they have not been fetched."""
        return self._era_vocabulary

    def remember_era_vocabulary(self, names: tuple[str, ...]) -> None:
        """Keep the era names so the rest of the day does not look them up again."""
        self._era_vocabulary = names

    def next_turn_index(self) -> int:
        """The number the next turn will carry."""
        return self._counted

    def record_turn(self, turn: ChatTurn) -> None:
        """Add a turn to the day and mark the session as active."""
        self._turns.append(turn)
        self._counted = max(self._counted, turn.turn_index + 1)
        self.last_activity_at = turn.timestamp

    def recent_turns(self, count: int) -> list[ChatTurn]:
        """
        The last few turns, oldest first.

        Asking for none, or for a negative number, gives back nothing rather
        than everything — a window of zero should mean no context, and
        slicing would quietly turn it into the whole conversation.
        """
        if count <= 0:
            return []
        turns = list(self._turns)
        return turns[-count:]

    def unlock(self, domain: Domain) -> None:
        """Record that the person opened this area of life themselves."""
        if domain not in self._unlocked:
            self._unlocked.add(domain)
            logger.info(
                "a sensitive area was opened by the user and is now available",
                extra={"session_id": self.session_id, "domain": domain.value},
            )

    def is_unlocked(self, domain: Domain) -> bool:
        """Whether this area has been opened today."""
        return domain in self._unlocked


class SessionRegistry:
    """
    The live sessions, one per person and label.

    Asking for a session on a date the held one does not cover replaces it.
    That is the whole of the midnight rule: there is no timer and no
    background sweep, because a session nobody is talking in does not need
    closing — it needs to be gone the next time somebody talks.

    The current time is always passed in rather than read from the clock
    here, so the day boundary can be exercised without pretending it is
    midnight.
    """

    def __init__(self, config: QueryConfig | None = None) -> None:
        self._config = config or QueryConfig()
        self._sessions: dict[tuple[str, str], ChatSession] = {}

    def open(
        self, user_id: str, *, at: datetime, label: str = DEFAULT_LABEL
    ) -> ChatSession:
        """
        The session for this person's conversation right now, starting a new
        one if the day has turned over.
        """
        key = (user_id, label)
        today = at.date()
        existing = self._sessions.get(key)

        if existing is not None and existing.event_date == today:
            return existing

        if existing is not None:
            logger.info(
                "the day changed, so today starts with an empty session",
                extra={
                    "closed_session_id": existing.session_id,
                    "closed_turns": existing.turn_count,
                },
            )

        session = ChatSession(
            session_id=make_session_id(user_id, today, label),
            user_id=user_id,
            event_date=today,
            session_label=label,
            created_at=at,
            last_activity_at=at,
            max_turns=self._config.session_max_turns,
            context_buffer=SessionContextBuffer(
                max_entries=self._config.session_buffer_size,
                max_idle_turns=self._config.session_buffer_max_idle_turns,
            ),
        )
        self._sessions[key] = session
        return session

    def get(self, session_id: str) -> ChatSession | None:
        """The held session with this identifier, if there is one."""
        return next(
            (
                session
                for session in self._sessions.values()
                if session.session_id == session_id
            ),
            None,
        )

    def close(self, session_id: str) -> None:
        """Forget a session. Doing this to one that is already gone is fine."""
        for key, session in list(self._sessions.items()):
            if session.session_id == session_id:
                del self._sessions[key]
                logger.info(
                    "a session was closed",
                    extra={"session_id": session_id, "turns": session.turn_count},
                )
                return

    def close_all(self) -> None:
        """Forget every session. Used when a process is shutting down."""
        self._sessions.clear()


__all__ = ["ChatSession", "SessionRegistry", "make_session_id", "DEFAULT_LABEL"]
