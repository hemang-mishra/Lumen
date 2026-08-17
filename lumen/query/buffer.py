"""
What today's conversation has already been told.

Every turn is searched for on its own, and on its own that is enough to
answer the turn. It is not enough to hold a conversation. Somebody spends
the afternoon realising that effort can feel like curiosity rather than
fear, and in the evening starts describing where the fear began — the same
story from both ends. A search that reads only the evening sentence finds
the beginning and never the afternoon, and the one connection worth making
is the one nobody makes.

So a short list of what has already been surfaced today is kept, and every
turn is checked against it. Anything still relevant is offered again, and
counted for a little more than it would be on its own, because something
this conversation has already been round once is more likely to matter than
something arriving cold.

Three rules keep it short. It holds five records. Anything nobody comes back
to for five turns drops out. And the heaviest records — the ones marked
CRITICAL — are never pushed out mid-conversation once they have been raised,
because those are the ones a person circles for an hour before saying
anything more about.

Nothing here is saved. The list is worth exactly one day, and tomorrow
starts empty.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from lumen.schemas.enums import SignalStrength

logger = logging.getLogger(__name__)

DEFAULT_MAX_ENTRIES = 5
DEFAULT_MAX_IDLE_TURNS = 5


@dataclass
class BufferEntry:
    """
    One record this conversation has already seen today.

    The vector is the interesting field. It is the record's own position in
    the search index, fetched once when the record joined the list, and it
    is what makes checking relevance each turn a piece of arithmetic rather
    than another search. A record whose vector could not be read keeps None
    and is compared by words instead — worse, but never nothing.

    Attributes:
        node_id: Which record this is.
        node_type: What kind of record.
        preview: Its readable text, used for the word-overlap fallback.
        signal_strength: How much it weighs. CRITICAL is protected.
        first_seen_turn: The turn it was first surfaced on.
        last_relevant_turn: The last turn it still applied to.
        vector: Its position in the search index, if that could be read.
        properties: The rest of the record, carried so re-offering it does
            not mean reading the graph again.
    """

    node_id: str
    node_type: str
    preview: str
    signal_strength: SignalStrength = SignalStrength.STANDARD
    first_seen_turn: int = 0
    last_relevant_turn: int = 0
    vector: tuple[float, ...] | None = None
    properties: dict = field(default_factory=dict, repr=False)

    @property
    def protected(self) -> bool:
        """Whether this record is too heavy to be dropped mid-conversation."""
        return self.signal_strength is SignalStrength.CRITICAL

    def idle_for(self, turn_index: int) -> int:
        """How many turns have passed since this last applied."""
        return max(turn_index - self.last_relevant_turn, 0)


class SessionContextBuffer:
    """
    The few records today's conversation keeps in mind.

    Deliberately small. This is not a cache and making it bigger would not
    make it better — its whole value is that it holds the thread of *this*
    conversation, and a list of thirty things is not a thread.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_idle_turns: int = DEFAULT_MAX_IDLE_TURNS,
    ) -> None:
        self._max_entries = max(int(max_entries), 0)
        self._max_idle_turns = max(int(max_idle_turns), 0)
        self._entries: dict[str, BufferEntry] = {}

    @property
    def entries(self) -> tuple[BufferEntry, ...]:
        """Everything currently held, most recently relevant first."""
        return tuple(
            sorted(
                self._entries.values(),
                key=lambda entry: (entry.last_relevant_turn, entry.node_id),
                reverse=True,
            )
        )

    @property
    def node_ids(self) -> tuple[str, ...]:
        """The identifiers held, for checking membership cheaply."""
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._entries

    # ------------------------------------------------------------------
    # Putting things in
    # ------------------------------------------------------------------

    def remember(self, entries: Iterable[BufferEntry], *, turn_index: int) -> None:
        """
        Record that these came up on this turn.

        Something already held is refreshed rather than added again, which
        is what keeps a subject the conversation keeps returning to from
        ageing out while it is still being discussed.

        A new record arriving at a full list displaces the one nobody has
        come back to for longest — unless every one of them is protected, in
        which case nothing is displaced and the new record simply does not
        join. It was still offered to the AI on this turn; all it loses is
        being offered again unprompted later.
        """
        for entry in entries:
            held = self._entries.get(entry.node_id)
            if held is not None:
                held.last_relevant_turn = max(held.last_relevant_turn, turn_index)
                if held.vector is None:
                    held.vector = entry.vector
                continue

            if len(self._entries) >= self._max_entries and not self._make_room():
                logger.debug(
                    "today's thread is full of material too heavy to drop, so "
                    "this record was offered but not kept",
                    extra={"node_id": entry.node_id, "held": len(self._entries)},
                )
                continue

            entry.first_seen_turn = turn_index
            entry.last_relevant_turn = turn_index
            self._entries[entry.node_id] = entry

    def mark_relevant(self, node_ids: Iterable[str], *, turn_index: int) -> None:
        """Note that these still applied on this turn, without adding any."""
        for node_id in node_ids:
            entry = self._entries.get(node_id)
            if entry is not None:
                entry.last_relevant_turn = max(entry.last_relevant_turn, turn_index)

    # ------------------------------------------------------------------
    # Taking things out
    # ------------------------------------------------------------------

    def evict_stale(self, turn_index: int) -> tuple[str, ...]:
        """
        Drop whatever the conversation has moved on from.

        Protected records stay. Somebody who raises the hardest thing in
        their history and then talks about work for ten minutes has not
        stopped being in the middle of it.
        """
        dropped = tuple(
            node_id
            for node_id, entry in self._entries.items()
            if not entry.protected and entry.idle_for(turn_index) >= self._max_idle_turns
        )
        for node_id in dropped:
            del self._entries[node_id]
        if dropped:
            logger.debug(
                "some of today's thread was let go",
                extra={"dropped": len(dropped), "turn_index": turn_index},
            )
        return dropped

    def clear(self) -> None:
        """Forget everything. Used when a day ends."""
        self._entries.clear()

    def _make_room(self) -> bool:
        """
        Drop the least-missed record, and say whether that was possible.

        Least-missed means the one nobody has come back to for longest.
        Protected records are not candidates, which is why this can fail.
        """
        droppable = [entry for entry in self._entries.values() if not entry.protected]
        if not droppable:
            return False
        oldest = min(
            droppable, key=lambda entry: (entry.last_relevant_turn, entry.node_id)
        )
        del self._entries[oldest.node_id]
        return True

    # ------------------------------------------------------------------
    # Asking what still applies
    # ------------------------------------------------------------------

    def relevant_to(
        self,
        *,
        vector: Sequence[float] | None,
        keywords: Sequence[str],
        threshold: float,
    ) -> list[tuple[BufferEntry, float]]:
        """
        Which of today's records still apply, and how strongly.

        Measured against the search text this turn produced, using each
        record's own position in the index. When there is no such position —
        the search could not run, or the record was never indexed — the
        comparison falls back to how many of the turn's words appear in the
        record. That is a much blunter instrument and it is used rather than
        skipped, because a conversation losing its thread is a worse failure
        than a slightly wrong measure of relevance.
        """
        scored = [
            (entry, self._closeness(entry, vector, keywords))
            for entry in self._entries.values()
        ]
        kept = [(entry, score) for entry, score in scored if score >= threshold]
        return sorted(kept, key=lambda pair: pair[1], reverse=True)

    def _closeness(
        self,
        entry: BufferEntry,
        vector: Sequence[float] | None,
        keywords: Sequence[str],
    ) -> float:
        """How much this held record has to do with what was just said."""
        if vector is not None and entry.vector is not None:
            return cosine(vector, entry.vector)
        return word_overlap(keywords, entry.preview)


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """
    How closely two positions in the index point the same way, from 0 to 1.

    Vectors of different widths give zero rather than raising. That only
    happens when the embedding model changed under a running process, which
    is a configuration problem reported elsewhere — it should cost this turn
    its continuity, not the conversation.
    """
    if len(left) != len(right) or not left:
        return 0.0

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_size = math.sqrt(sum(a * a for a in left))
    right_size = math.sqrt(sum(b * b for b in right))
    if left_size == 0.0 or right_size == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_size * right_size)))


def word_overlap(keywords: Sequence[str], text: str) -> float:
    """
    What share of the turn's words appear in a record, from 0 to 1.

    The fallback comparison, used only when one side has no position in the
    index. Crude on purpose: anything cleverer here would be a second
    ranking system to keep honest, and this one only has to be good enough
    to notice that two sentences are about the same thing.
    """
    wanted = {word.strip().casefold() for word in keywords if word and word.strip()}
    if not wanted:
        return 0.0
    haystack = text.casefold()
    hits = sum(1 for word in wanted if word in haystack)
    return hits / len(wanted)


__all__ = [
    "BufferEntry",
    "SessionContextBuffer",
    "cosine",
    "word_overlap",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_IDLE_TURNS",
]
