"""
Spreading requests across several credentials for the same vendor.

Cloud model quotas are counted per key, per minute. One key is a single meter
that a pipeline run — dozens of extraction calls and a few hundred embeddings
for one evening's journalling — walks straight into. Several keys are several
meters, and the only thing standing between them and the work is deciding
which one a given request uses.

That decision is all this module does, and it is deliberately vendor-neutral:
a pool holds opaque strings and hands one back. Which SDK client those strings
end up building is the provider's business, not the pool's.

Two ways of choosing, both defensible:

  - random, the default. Holds no state, so it stays correct across threads,
    processes and workers that never see each other. Over a run of any length
    the spread is even enough, and nothing has to be shared to keep it that way.
  - round_robin, when the request count is small enough that random choice
    could plausibly land on one key several times in a row. It keeps a counter
    behind a lock, which only balances within a single process.

Keys are never logged. A pool reports positions ("key 2 of 5"), which is
enough to follow a rotation through a log file and useless to anyone who
finds it there.
"""

from __future__ import annotations

import itertools
import logging
import random
import threading

logger = logging.getLogger(__name__)

# The two ways of picking. Anything else is refused at construction rather
# than silently treated as the default, because a typo in a deployment
# variable should be loud.
RANDOM = "random"
ROUND_ROBIN = "round_robin"
STRATEGIES = (RANDOM, ROUND_ROBIN)


class ApiKeyPool:
    """
    A set of interchangeable credentials and a rule for choosing between them.

    Construct one with the keys in the order they were configured; position
    numbers in logs refer to that order. Duplicates and blank entries are
    dropped on the way in, so a .env with a trailing comma or the same key
    pasted twice does not quietly halve the benefit of rotating.
    """

    def __init__(
        self,
        keys: list[str] | tuple[str, ...],
        *,
        strategy: str = RANDOM,
        random_source: random.Random | None = None,
    ) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(
                f"unknown key rotation strategy {strategy!r}; expected one of {', '.join(STRATEGIES)}"
            )

        self._keys = _clean(keys)
        if not self._keys:
            raise ValueError("an ApiKeyPool needs at least one key")

        self._strategy = strategy
        self._random = random_source or random.Random()
        self._counter = itertools.count()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def strategy(self) -> str:
        """Which rule this pool is choosing by."""
        return self._strategy

    @property
    def keys(self) -> tuple[str, ...]:
        """The keys, in configured order. Callers must not log these."""
        return self._keys

    def select(self, *, exclude: str | None = None) -> str:
        """
        Choose a key for one request.

        Args:
            exclude: A key to avoid if there is any alternative — the one a
                failing request just used. This is what makes a retry after a
                rate limit land somewhere new instead of hammering the meter
                that is already empty. With a single key configured it is
                ignored, because avoiding it would mean not calling at all.

        Returns:
            One of the configured keys.
        """
        avoiding = exclude if len(self._keys) > 1 else None

        if self._strategy == ROUND_ROBIN:
            with self._lock:
                key = self._at(next(self._counter))
                # Skipping a turn rather than choosing from a shortened list:
                # indexing into a filtered list would shift every subsequent
                # position and turn an even walk into a lopsided one.
                if key == avoiding:
                    key = self._at(next(self._counter))
            return key

        candidates = tuple(key for key in self._keys if key != avoiding)
        return self._random.choice(candidates)

    def _at(self, index: int) -> str:
        """The key at a position in the walk, wrapping round the end."""
        return self._keys[index % len(self._keys)]

    def position_of(self, key: str) -> int:
        """
        Where a key sits in the configured order, counting from 1.

        Returns 0 for a key this pool does not hold, which cannot happen for a
        key it just handed out and is only worth guarding because the answer
        goes into a log line.
        """
        try:
            return self._keys.index(key) + 1
        except ValueError:
            return 0

    def label_for(self, key: str) -> str:
        """A key's position rendered for a log line, never the key itself."""
        return f"{self.position_of(key)}/{len(self._keys)}"


def _clean(keys: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Drop blanks and repeats, keeping the order they were configured in."""
    seen: dict[str, None] = {}
    for key in keys:
        stripped = (key or "").strip()
        if stripped:
            seen.setdefault(stripped, None)
    return tuple(seen)


__all__ = ["ApiKeyPool", "RANDOM", "ROUND_ROBIN", "STRATEGIES"]
