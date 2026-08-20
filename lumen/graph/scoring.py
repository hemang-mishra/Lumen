"""
What a stored record is worth when somebody's history is searched.

Finding a record is only half of retrieval. The other half is deciding which
of the things found is most worth putting in front of a person, and that is
never just "whichever matched the words best". Four things change the answer:

  how strong a signal it was      — a life-changing realisation outranks a
                                    passing note worded the same way
  how long ago it was last true   — somebody who reaffirmed a belief last
                                    week is not the same as somebody who last
                                    mentioned it three years ago
  who said it                     — something the person put in their own
                                    words counts for more than something an
                                    assistant suggested and they never
                                    confirmed
  how often it has helped         — a record that keeps turning out to be the
                                    relevant one probably is

They live together here, away from any particular search, because more than
one part of Lumen ranks records. If two of them held their own opinions about
what a record is worth, the same record would come out in a different place
depending on who asked, and a report could end up stating a number that
nothing actually uses.

Nothing here reaches a database or a clock. Everything is a function of a row
that was already read and a moment that was already decided, which is what
makes every number below reproducible by hand.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lumen.config import ScoringConfig
from lumen.graph.rows import SIGNAL_WEIGHT, as_utc, last_seen_at, signal_of
from lumen.schemas.enums import PatternAgeBand, VerificationStatus

# What something counts for when there is no reason to discount it.
FULL_WEIGHT = 1.0

# The order the bands run in, oldest last. Used to turn a number of quiet
# days into a band without a chain of comparisons that could drift apart from
# the table of what each band is worth.
BAND_ORDER: tuple[PatternAgeBand, ...] = (
    PatternAgeBand.FRESH,
    PatternAgeBand.COOLING,
    PatternAgeBand.STALE,
    PatternAgeBand.DORMANT,
)


@dataclass(frozen=True)
class Thresholds:
    """
    The day counts that separate the bands, guaranteed to be in order.

    Read straight from settings they could be anything — somebody could set
    the dormant mark earlier than the cooling one and produce a band no
    record could ever fall into. Sorting them on the way in means a strange
    setting produces a strange curve rather than an unreachable one.
    """

    fresh: int
    cooling: int
    dormant: int

    @classmethod
    def of(cls, config: ScoringConfig) -> "Thresholds":
        """The thresholds these settings describe, put in ascending order."""
        fresh = max(int(config.fresh_days), 0)
        cooling = max(int(config.cooling_days), fresh)
        dormant = max(int(config.dormant_days), cooling)
        return cls(fresh=fresh, cooling=cooling, dormant=dormant)

    def band_for(self, quiet_days: int) -> PatternAgeBand:
        """Which band this many days of quiet falls into."""
        if quiet_days < self.fresh:
            return PatternAgeBand.FRESH
        if quiet_days < self.cooling:
            return PatternAgeBand.COOLING
        if quiet_days < self.dormant:
            return PatternAgeBand.STALE
        return PatternAgeBand.DORMANT


@dataclass(frozen=True)
class RecordWeights:
    """
    Everything that changed one record's place in the ranking, kept apart.

    The ranking only needs the product, but a single number cannot answer
    "why is this third?". Keeping the four parts means the reason a record
    ranked where it did is readable afterwards instead of being lost the
    moment it is multiplied out.
    """

    signal: float = FULL_WEIGHT
    recency: float = FULL_WEIGHT
    trust: float = FULL_WEIGHT
    frequency: float = FULL_WEIGHT
    band: PatternAgeBand = PatternAgeBand.FRESH
    last_seen: datetime | None = None
    quiet_days: int = 0

    @property
    def multiplier(self) -> float:
        """The four parts as the single number a ranking uses."""
        return self.signal * self.recency * self.trust * self.frequency

    def applied_to(self, base: float) -> float:
        """
        This record's score, starting from how good the match was.

        A negative starting point is treated as no match rather than being
        multiplied into something stranger; a score is never below zero.
        """
        return max(float(base), 0.0) * self.multiplier


def band_weight(band: PatternAgeBand, config: ScoringConfig) -> float:
    """
    What a record in this band counts for.

    Never zero, in any band. A record that has gone quiet ranks lower and
    stays reachable — people do not stop having been a certain way because
    they stopped writing about it, and a search that dropped old material
    would lose exactly the long-running things worth keeping.
    """
    return {
        PatternAgeBand.FRESH: FULL_WEIGHT,
        PatternAgeBand.COOLING: float(config.cooling_weight),
        PatternAgeBand.STALE: float(config.stale_weight),
        PatternAgeBand.DORMANT: float(config.dormant_weight),
    }[band]


def quiet_days(last_seen: datetime | None, now: datetime) -> int:
    """
    How many days a record has gone without being seen again.

    A record with no readable date, or one dated in the future, counts as
    quiet for no days at all. Both are the cautious direction: neither a
    missing date nor a clock disagreement should be read as "very old" and
    cost a record its place.
    """
    if last_seen is None:
        return 0
    return max((as_utc(now) - as_utc(last_seen)).days, 0)


def age_band(
    last_seen: datetime | None, now: datetime, *, config: ScoringConfig
) -> PatternAgeBand:
    """Which band a record falls into, given when it was last seen."""
    return Thresholds.of(config).band_for(quiet_days(last_seen, now))


def recency_weight(
    last_seen: datetime | None, now: datetime, *, config: ScoringConfig
) -> float:
    """
    What a record's age costs it.

    Turned off entirely by one setting, so the ranking with and without time
    in it can be compared without touching any code.
    """
    if not config.decay_enabled:
        return FULL_WEIGHT
    return band_weight(age_band(last_seen, now, config=config), config)


def trust_weight(row: Mapping[str, Any], *, config: ScoringConfig) -> float:
    """
    What it counts for that the person confirmed this themselves.

    Lumen writes down insights that came out of a conversation with itself.
    If those ranked equally with what the person said in their own words, the
    system would slowly start quoting itself back and calling it their
    history. So an unconfirmed suggestion counts for less until they confirm
    it.

    Anything unreadable is treated as the person's own. That can only fail to
    demote something, never demote something that did not deserve it.
    """
    try:
        status = VerificationStatus(str(row.get("verification_status") or "IMPLICIT"))
    except ValueError:
        return FULL_WEIGHT
    if status is VerificationStatus.UNVERIFIED:
        return float(config.unverified_weight)
    return FULL_WEIGHT


def frequency_weight(row: Mapping[str, Any], *, config: ScoringConfig) -> float:
    """
    What it counts for that this record keeps turning out to be the useful one.

    Capped, and the cap is the whole reason this is safe to have. Being shown
    makes a record more likely to be shown again, so without a ceiling the
    lift would compound until one record crowded out everything else.
    """
    if not config.frequency_enabled:
        return FULL_WEIGHT
    hits = _counter(row.get("query_frequency"))
    lift = FULL_WEIGHT + max(float(config.frequency_step), 0.0) * hits
    return min(lift, max(float(config.frequency_cap), FULL_WEIGHT))


def weigh(
    row: Mapping[str, Any], *, now: datetime, config: ScoringConfig
) -> RecordWeights:
    """
    Everything that changes one record's worth, worked out in one place.

    Callers that only want the number use `applied_to`; callers explaining a
    ranking read the parts.
    """
    last_seen = last_seen_at(dict(row))
    quiet = quiet_days(last_seen, now)
    band = Thresholds.of(config).band_for(quiet)
    return RecordWeights(
        signal=SIGNAL_WEIGHT[signal_of(dict(row))],
        recency=band_weight(band, config) if config.decay_enabled else FULL_WEIGHT,
        trust=trust_weight(row, config=config),
        frequency=frequency_weight(row, config=config),
        band=band,
        last_seen=last_seen,
        quiet_days=quiet,
    )


def final_score(
    base: float, row: Mapping[str, Any], *, now: datetime, config: ScoringConfig
) -> float:
    """
    A record's score, starting from how good the match was.

    For a match measured by meaning that starting point is the closeness. For
    one found by an exact anchor — this person's name, this period of a life —
    it is a fixed base, because matching a name is not something that has a
    distance.
    """
    return weigh(row, now=now, config=config).applied_to(base)


def _counter(value: Any) -> int:
    """A stored counter as a whole number, treating anything odd as none."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "FULL_WEIGHT",
    "BAND_ORDER",
    "Thresholds",
    "RecordWeights",
    "band_weight",
    "quiet_days",
    "age_band",
    "recency_weight",
    "trust_weight",
    "frequency_weight",
    "weigh",
    "final_score",
]
