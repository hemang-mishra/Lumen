"""
The shapes a turn's search hands back.

Two models carry everything. A `RetrievedNode` is one record worth knowing
about, together with how it was found — which matters, because a record
found by name and a record found by resemblance deserve different amounts of
trust. A `RetrievalBundle` is everything decided for one turn, including the
parts that did not work.

The reports on the bundle are not decoration. Three searches run and any of
them can fail on its own, and every one of them answers an empty list when
it finds nothing. Without a per-search account, "this person has no history
about their brother" and "the anchor lookup threw" arrive identical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.enums import (
    Domain,
    RetrievalOutcome,
    RetrievalPass,
    SignalStrength,
    StructuralAnchorType,
    TriggerType,
)


class RetrievedNode(BaseModel):
    """
    One record from the person's history, offered for this turn.

    Attributes:
        node_id: Which record this is.
        node_type: What kind of record.
        preview: Its readable text, shortened.
        found_by: Which of the three searches surfaced it.
        trigger_type: Which reason to search led here, when one did.
        similarity: How closely it matched, when that was measured. Left
            unset for records found by an anchor, because an anchor match is
            not a measurement and giving it a number would invite somebody
            to compare the two as though it were.
        signal_strength: How much the record weighs.
        domain: The area of life it belongs to, for the records that name
            one.
        era_tag: The period of the past it belongs to, likewise.
        occurred_at: When it happened, where the record says.
        anchor_type: Which kind of anchor led here, for structural finds.
        anchor_value: The anchor itself — the name, the era.
        boosted: True when this was already part of today's conversation.
        rank_score: The provisional ordering. Closeness times the record's
            own weight times the continuity boost. Time decay is not in it,
            and the final ordering is not decided here.
        properties: The rest of the record. Carried because compressing it
            into a briefing needs columns that differ by kind, and reading
            the graph a second time inside a three-second budget to fetch
            rows already in hand would be the wrong trade.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    preview: str = Field(min_length=1)
    found_by: RetrievalPass
    trigger_type: TriggerType | None = None
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    signal_strength: SignalStrength = SignalStrength.STANDARD
    domain: Domain | None = None
    era_tag: str | None = None
    occurred_at: datetime | None = None
    anchor_type: StructuralAnchorType | None = None
    anchor_value: str | None = None
    boosted: bool = False
    rank_score: float = Field(default=0.0, ge=0.0)
    properties: dict[str, Any] = Field(default_factory=dict, repr=False)


class PassReport(BaseModel):
    """
    What one of the three searches did.

    Attributes:
        which: Which search this was.
        ran: False when it was skipped or never got to start.
        found: How many records it turned up before anything was cut.
        kept: How many of those survived into the answer.
        duration_ms: How long it took.
        failure: A short word for what went wrong, or nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    which: RetrievalPass
    ran: bool = True
    found: int = Field(default=0, ge=0)
    kept: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    failure: str | None = None


class RetrievalBundle(BaseModel):
    """
    Everything found for one turn, and everything that stopped it.

    Attributes:
        session_id: The day's conversation this belongs to.
        turn_index: Which turn was searched for.
        outcome: The short version — found something, found nothing, was not
            needed, was suppressed, or could not run.
        candidates: The records, best first.
        passes: What each of the three searches did.
        latency_ms: How long the whole search took.
        within_budget: False when the deadline passed with work outstanding.
            The turn is not held up either way; this is what lets the layer
            above decide between using it now and carrying it to the next
            turn.
        gated: Records withheld because they are the most sensitive kind and
            the person has not opened that subject today. Named rather than
            silently dropped, because a system that quietly withholds things
            is one nobody can debug.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    turn_index: int = Field(ge=0)
    outcome: RetrievalOutcome = RetrievalOutcome.NOTHING
    candidates: tuple[RetrievedNode, ...] = ()
    passes: tuple[PassReport, ...] = ()
    latency_ms: int = Field(default=0, ge=0)
    within_budget: bool = True
    gated: tuple[str, ...] = ()

    @property
    def search_failed(self) -> bool:
        """
        Whether nothing could be looked up, as opposed to nothing existing.

        True only when every search that actually had a store to consult
        failed. One working search is enough to say the graph was asked.
        """
        return consulted_nothing(self.passes)


def store_searches(reports: tuple[PassReport, ...]) -> list[PassReport]:
    """
    The searches that had a store to consult and something to ask it.

    Two kinds of report are left out, and both would otherwise make a turn
    that consulted nothing look as though it had. Today's thread is memory,
    not a store — it answers whether or not the graph is reachable. And a
    search with no work to do never touched anything: a reason with no
    anchor half leaves the anchor lookups with nothing to run, which is not
    the same as running them and finding nothing.
    """
    return [
        report
        for report in reports
        if report.which is not RetrievalPass.CONTINUITY and report.ran
    ]


def consulted_nothing(reports: tuple[PassReport, ...]) -> bool:
    """Whether every search that had a store to ask came back unable to ask it."""
    attempted = store_searches(reports)
    return bool(attempted) and all(
        report.failure is not None for report in attempted
    )


class PassAResult(BaseModel):
    """
    What the meaning-based search produced, plus the vector it used.

    The vector is carried out of the pass because the continuity check needs
    it. That check asks how close today's earlier records are to what was
    just said — the same measurement this pass already made against the same
    text — and asking a model to make it a second time would double the cost
    of a turn to learn nothing new.

    Attributes:
        candidates: The records found, already ranked and cut.
        query_vector: The position this turn was searched from, if the
            search text could be embedded at all.
        found: How many matches came back before filtering.
        used_fallback: True when the search text is the turn's own words
            because the model call did not work out.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[RetrievedNode, ...] = ()
    query_vector: tuple[float, ...] | None = None
    found: int = Field(default=0, ge=0)
    used_fallback: bool = False


class Hypothetical(BaseModel):
    """
    A made-up historical record that would perfectly answer one reason to
    search.

    Never stored, never shown to anyone, never treated as something the
    person said. It exists to be turned into a vector, because what somebody
    says out loud and what their own past record of it says are written
    completely differently — and an invented version of the record sits much
    closer to the real one than the spoken sentence does.

    Attributes:
        index: Which reason it belongs to, counting from one.
        text: The invented record.
    """

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    text: str = ""


class HydeReply(BaseModel):
    """What the model returns when asked for one invented record per reason."""

    model_config = ConfigDict(extra="ignore")

    hypotheticals: list[Hypothetical] = Field(default_factory=list)


class SearchText(BaseModel):
    """
    The text each reason is searched with, in the order the reasons arrived.

    Attributes:
        texts: One per reason. Never shorter than the list of reasons — a
            missing invention is replaced by the turn's own words rather
            than dropped, because a shifted list means every later reason is
            searched with the wrong text, and that failure is invisible.
        used_fallback: True when nothing was invented and every reason is
            being searched with what the person actually said.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    texts: tuple[str, ...] = ()
    used_fallback: bool = False


__all__ = [
    "RetrievedNode",
    "PassReport",
    "RetrievalBundle",
    "PassAResult",
    "Hypothetical",
    "HydeReply",
    "SearchText",
    "store_searches",
    "consulted_nothing",
]
