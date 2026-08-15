"""
Turning checked findings into graph nodes.

By the time anything reaches this file it has already been judged: every
category is real and every rule has been satisfied. What is left is the
work of giving each finding an identity and a place in time — a name that
nothing else will ever share, the moment the described experience
happened, and the moment it was recorded.

All of that is decided here rather than asked for. The model is never
asked what today's date is, nor what to call a node, because it cannot
know either, and a confident wrong answer to a question about time
poisons everything that later depends on ordering.

One node is created here that nothing in the entry asked for. A belief is
not allowed to change in the graph without something to explain why it
changed — an event, or a session in which the thinking happened. A
reflective entry often has no event in it at all, so the session is minted
directly, and the guarantee holds without a model having to agree that it
should.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lumen.pipeline.extraction.contracts import RejectedItem
from lumen.pipeline.extraction.validation import (
    CleanChain,
    CleanEvent,
    CleanObservation,
    flatten,
)
from lumen.schemas.enums import (
    EntryClass,
    ExtractionConfidence,
    NodeStatus,
    ObservationStatus,
    ObservationType,
    Provenance,
    SignalStrength,
)
from lumen.schemas.ids import make_scoped_node_id
from lumen.schemas.nodes import (
    CausalChainNode,
    CausalStepNode,
    EventNode,
    ObservationNode,
    SessionNode,
)
from lumen.schemas.pipeline import MicroextractionInput

# How much weight each level carries, so the strongest can be found.
_SIGNAL_ORDER: dict[SignalStrength, int] = {
    SignalStrength.STANDARD: 0,
    SignalStrength.HIGH: 1,
    SignalStrength.CRITICAL: 2,
}

# Who took part in a reflective session. The assistant is only listed when
# something it said was actually taken up.
_SELF = "user"
_ASSISTANT = "ai_facilitator"


class _IdMinter:
    """
    Hands out node names that cannot collide.

    Each episode is extracted by its own separate call, and every one of
    them counts its nodes from one. Putting the episode's position into the
    name is what keeps the second episode of a day from claiming names the
    first already used, without the two calls needing to know about each
    other.
    """

    def __init__(self, payload: MicroextractionInput) -> None:
        self._event_date = payload.event_date
        self._episode_index = payload.episode.episode_index
        self._counters: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        """Give out the next name for this kind of node."""
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return make_scoped_node_id(
            prefix, self._event_date, self._episode_index, self._counters[prefix]
        )


class NodeFactory:
    """
    Builds the graph nodes for one episode.

    Holds the few facts every node from an episode shares — when the
    experience happened, which episode it belongs to, which model read it —
    so that each build method only deals with what is actually different
    about its own kind of node.

    Everything built by one factory shares a single recording time, taken
    once when the factory is made. Reading the clock per node would scatter
    the contents of one entry across a smear of timestamps and make the
    ordering of things within it meaningless.
    """

    def __init__(
        self,
        payload: MicroextractionInput,
        *,
        extraction_model: str,
        recorded_at: datetime | None = None,
    ) -> None:
        self._payload = payload
        self._episode = payload.episode
        self._model = extraction_model
        self._recorded_at = recorded_at or datetime.now(UTC)
        self._ids = _IdMinter(payload)
        self._adopted = tuple(
            flatten(span).strip() for span in payload.co_created_spans if span.strip()
        )

    # -- findings ---------------------------------------------------------

    def observations(
        self, items: tuple[CleanObservation, ...], *, attempt: int = 1
    ) -> list[ObservationNode]:
        """
        Build a node for every finding taken from the episode.

        Which attempt produced a finding is recorded on it, so that if
        corrected findings ever turn out to be systematically worse than
        first-attempt ones, the evidence is already in the graph rather
        than being a suspicion nobody can check.
        """
        return [self._observation(item, attempt) for item in items]

    def _observation(self, item: CleanObservation, attempt: int) -> ObservationNode:
        return ObservationNode(
            node_id=self._ids.next("obs"),
            created_at=self._recorded_at,
            valid_from=self._recorded_at,
            episode_id=self._episode.episode_id,
            occurred_at=self._payload.occurred_at,
            type=item.type,
            content=item.content,
            raw_evidence=list(item.raw_evidence),
            signal_strength=item.signal_strength,
            provenance=self._credit(item),
            extraction_confidence=item.extraction_confidence,
            person_refs=list(item.person_refs),
            status=self._observation_status(),
            extraction_model=self._model,
            extraction_attempt=attempt,
        )

    def _credit(self, item: CleanObservation) -> Provenance:
        """
        Decide whose idea a finding was.

        A finding built on wording the assistant supplied, and the person
        took up, belongs to both of them. Ideas credited that way are
        trusted a little less when the history is searched later, which is
        the point: the person agreed with it, but has not yet arrived at it
        on their own.

        The move only ever goes one way. A finding already credited as
        shared stays shared even when no wording matches, because raising
        its standing on the strength of a failed text match would quietly
        promote the assistant's ideas into the person's own history.
        """
        if item.provenance is not Provenance.USER_GENERATED:
            return item.provenance
        if self._rests_on_adopted_wording(item):
            return Provenance.CO_CREATED
        return Provenance.USER_GENERATED

    def _rests_on_adopted_wording(self, item: CleanObservation) -> bool:
        """True when the finding repeats wording the person took from the assistant."""
        if not self._adopted:
            return False
        haystack = flatten(" ".join((item.content, *item.raw_evidence)))
        return any(span and span in haystack for span in self._adopted)

    def _observation_status(self) -> ObservationStatus:
        """
        Mark findings from a thin entry as such.

        They are written straight to the graph and never compared against
        the person's history, so they have to be distinguishable from
        findings that were.
        """
        if self._episode.entry_class is EntryClass.RAW_CAPTURE:
            return ObservationStatus.RAW_CAPTURE
        return ObservationStatus.ACTIVE

    def failed_observation(self, rejected: RejectedItem) -> ObservationNode:
        """
        Build a node for a finding that could not be salvaged.

        It is kept rather than dropped so a person can be shown what the
        reading could not make sense of. That only works if their content
        survives untouched, so it does.

        The type is set to CONTEXT — the plainest "something happened"
        label — because the type is usually the very thing that was wrong,
        and it is the one field here that cannot be trusted. What the model
        actually tried, and the rule that refused it, are written into the
        evidence instead, so the review screen can show both without
        needing a second record to join against.
        """
        attempted = getattr(rejected.payload, "type", "") or "none given"
        notes = [
            f"extraction failed: {rejected.rule.value}",
            f"attempted type: {attempted}",
        ]
        if rejected.last_rule is not None and rejected.last_rule is not rejected.rule:
            notes.append(f"still wrong after correcting: {rejected.last_rule.value}")
        return ObservationNode(
            node_id=self._ids.next("obs"),
            created_at=self._recorded_at,
            valid_from=self._recorded_at,
            episode_id=self._episode.episode_id,
            occurred_at=self._payload.occurred_at,
            type=ObservationType.CONTEXT,
            content=_content_of(rejected),
            raw_evidence=notes,
            signal_strength=SignalStrength.STANDARD,
            provenance=Provenance.USER_GENERATED,
            extraction_confidence=ExtractionConfidence.STANDARD,
            status=ObservationStatus.EXTRACTION_FAILED,
            extraction_model=self._model,
            extraction_attempt=rejected.attempts,
        )

    # -- events -----------------------------------------------------------

    def events(self, items: tuple[CleanEvent, ...]) -> list[EventNode]:
        """Build a node for everything the person described as having happened."""
        return [
            EventNode(
                node_id=self._ids.next("evt"),
                created_at=self._recorded_at,
                valid_from=self._recorded_at,
                episode_id=self._episode.episode_id,
                occurred_at=self._payload.occurred_at,
                event_summary=item.event_summary,
                signal_strength=item.signal_strength,
                person_refs=list(item.person_refs),
                raw_evidence=list(item.raw_evidence),
                status=NodeStatus.ACTIVE,
            )
            for item in items
        ]

    # -- cause and effect --------------------------------------------------

    def chains(
        self, items: tuple[CleanChain, ...]
    ) -> tuple[list[CausalChainNode], list[CausalStepNode]]:
        """
        Build each sequence and its steps.

        The step count is counted here rather than taken from the reply.
        The model has been known to say six and send five, and a stored
        count that disagrees with the stored steps is worse than no count.
        """
        chains: list[CausalChainNode] = []
        steps: list[CausalStepNode] = []

        for item in items:
            chain_id = self._ids.next("chain")
            chains.append(
                CausalChainNode(
                    node_id=chain_id,
                    created_at=self._recorded_at,
                    valid_from=self._recorded_at,
                    episode_id=self._episode.episode_id,
                    chain_summary=item.chain_summary,
                    is_anticipatory=item.is_anticipatory,
                    step_count=len(item.steps),
                    status=NodeStatus.ACTIVE,
                )
            )
            steps.extend(
                CausalStepNode(
                    node_id=self._ids.next("step"),
                    created_at=self._recorded_at,
                    chain_id=chain_id,
                    step_index=position,
                    step_type=step.step_type,
                    content=step.content,
                    branch_id=step.branch_id,
                )
                for position, step in enumerate(item.steps, start=1)
            )

        return chains, steps

    # -- the anchor --------------------------------------------------------

    def session_anchor(self, observations: list[ObservationNode]) -> SessionNode:
        """
        Build the node representing the act of reflecting itself.

        Nothing in the entry asks for this. It exists because a belief can
        only be recorded as having changed if there is something to point
        at as the reason — an event, or a session of thinking. Plenty of
        real entries contain no event at all, only someone working
        something out, and without this node those entries could never
        record a change.

        Its summary is the one Stage 0 already wrote for the episode rather
        than a fresh one, so no second model call is spent restating what
        is already known.
        """
        return SessionNode(
            node_id=self._ids.next("sess"),
            created_at=self._recorded_at,
            valid_from=self._recorded_at,
            episode_id=self._episode.episode_id,
            occurred_at=self._payload.occurred_at,
            event_date=self._payload.event_date,
            session_label=self._payload.session_label or "A",
            session_summary=self._episode.episode_summary,
            signal_strength=strongest_signal(observations),
            status=NodeStatus.ACTIVE,
            participant_entities=self._participants(),
        )

    def _participants(self) -> list[str]:
        """Name who was in the session — the assistant only if it contributed."""
        if self._payload.co_created_spans:
            return [_SELF, _ASSISTANT]
        return [_SELF]


def _content_of(rejected: RejectedItem) -> str:
    """
    Find something readable to keep from a refused item.

    A finding carries its content, an event its summary, a sequence its
    one-line description. If the item arrived with none of those — which is
    itself one of the ways an item gets refused — a short note stands in,
    because a node has to say something and pretending otherwise would fail
    where it matters least.
    """
    for field_name in ("content", "event_summary", "chain_summary"):
        text = getattr(rejected.payload, field_name, "").strip()
        if text:
            return text
    return "The model returned an item with nothing readable in it."


def strongest_signal(observations: list[ObservationNode]) -> SignalStrength:
    """
    The heaviest weight carried by anything found in the episode.

    The session inherits it so that a session holding a life-defining
    realisation is not ranked alongside one about a slow afternoon.
    """
    if not observations:
        return SignalStrength.STANDARD
    return max(
        (item.signal_strength for item in observations),
        key=lambda strength: _SIGNAL_ORDER[strength],
    )


__all__ = ["NodeFactory", "strongest_signal"]
