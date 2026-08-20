"""
Fetching what a turn points at.

Reading the turn decided *whether* to look and *what for*. This does the
looking, three ways at once, inside a budget measured in seconds because
somebody is mid-conversation while it happens.

The order is not arbitrary:

  1. Nothing to look for, or somebody in acute distress → nothing happens,
     and it is recorded as which of those it was.
  2. The meaning-based search and the anchor lookups run side by side under
     one shared deadline. Whichever finishes, finishes; whichever does not
     is abandoned and reported as abandoned.
  3. Today's own thread is checked afterwards, using the measurement the
     first search has just made rather than paying for it twice.
  4. The heaviest records are held back unless the person has opened that
     subject themselves today.
  5. What is left is merged, ordered, cut, and remembered for later turns.

Nothing here writes to the graph. The stores arrive as a reader and an
index, and the only thing that changes is the day's memory of itself.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from lumen.config import QueryConfig, ScoringConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.query.deadline import Attempt, DeadlineRunner
from lumen.query.retrieval import continuity, gate, merge, semantic, structural
from lumen.query.retrieval.hydrate import Weighting
from lumen.query.retrieval.contracts import (
    PassAResult,
    PassReport,
    RetrievalBundle,
    RetrievedNode,
    consulted_nothing,
)
from lumen.query.session import ChatSession, LateArrival
from lumen.schemas.enums import EmotionalRegister, RetrievalOutcome, RetrievalPass
from lumen.schemas.query import RetrievalSignal
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)

SEMANTIC = "semantic"
STRUCTURAL = "structural"


class ConversationalRetriever:
    """
    Fetches a turn's history, three ways, under a deadline.

    An object rather than a plain function because it owns a pool of threads
    with a lifetime, exactly as the turn reader does. Everything else — the
    graph, the index, the models, the settings — is handed in, so a test can
    point it at temporary stores and a scripted model without changing
    anything about how it works.
    """

    def __init__(
        self,
        *,
        graph: ReadOnlyGraph,
        vectors: VectorProvider,
        embedder: EmbeddingProvider,
        llm: LLMProvider,
        config: QueryConfig | None = None,
        scoring: ScoringConfig | None = None,
        runner: DeadlineRunner | None = None,
    ) -> None:
        self._graph = graph
        self._vectors = vectors
        self._embedder = embedder
        self._llm = llm
        self._config = config or QueryConfig()
        self._scoring = scoring or ScoringConfig()
        self._owns_runner = runner is None
        self._runner = runner or DeadlineRunner(
            max_workers=self._config.retrieval_max_workers, name="retrieve"
        )

    def retrieve(
        self,
        signal: RetrievalSignal,
        session: ChatSession,
        *,
        now: datetime | None = None,
    ) -> RetrievalBundle:
        """
        Find what this turn's reasons point at.

        A turn with no reasons costs nothing at all — no thread, no model,
        no query. That is the common case and the whole point of deciding
        first and searching second.

        The moment the turn happened is fixed once here and used by every
        search, so all three age their records against the same instant.
        """
        started = time.perf_counter()

        if not signal.should_retrieve:
            return self._nothing(signal, started, _why_nothing(signal))

        weighting = Weighting.at(now, config=self._scoring)
        turn_text = _turn_text(session, signal.turn_index)
        carried = self._collect_carried(session, signal.turn_index)
        semantic_attempt, structural_attempt = self._run_searches(
            signal, turn_text, session, weighting
        )

        found_a: PassAResult = (
            semantic_attempt.value
            if semantic_attempt.ok and semantic_attempt.value is not None
            else PassAResult()
        )
        found_b: list[RetrievedNode] = (
            structural_attempt.value
            if structural_attempt.ok and structural_attempt.value is not None
            else []
        )

        revisited, boosts = continuity.revisit(
            session.context_buffer,
            already_found={
                node.node_id for node in (*found_a.candidates, *found_b)
            },
            query_vector=found_a.query_vector,
            keywords=_keywords(signal),
            config=self._config,
            weighting=weighting,
        )

        # Anything carried from last turn goes through the sensitivity rules
        # again rather than being trusted because it was fetched once. The
        # pass it came from never finished, so it was never checked at all —
        # and what the person has opened up may have changed since.
        allowed, withheld = gate.apply(
            [*found_a.candidates, *found_b, *revisited, *carried],
            unlocked=signal.unlocked_domains,
        )
        kept = merge.merge(
            allowed,
            boosts=boosts,
            boost_multiplier=self._config.session_boost_multiplier,
            cap=self._config.conversational_candidate_cap,
        )

        self._remember(kept, session=session, turn_index=signal.turn_index)

        reports = (
            _report(
                RetrievalPass.SEMANTIC,
                semantic_attempt,
                found_a.found,
                len(found_a.candidates),
            ),
            _report(
                RetrievalPass.STRUCTURAL,
                structural_attempt,
                len(found_b),
                len(found_b),
                had_work=structural.has_anchors(signal.retrieval_triggers),
            ),
            PassReport(
                which=RetrievalPass.CONTINUITY,
                found=len(boosts),
                kept=len(revisited),
            ),
        )
        bundle = RetrievalBundle(
            session_id=signal.session_id,
            turn_index=signal.turn_index,
            outcome=_outcome(kept, reports),
            candidates=tuple(kept),
            passes=reports,
            latency_ms=_elapsed_ms(started),
            within_budget=not (semantic_attempt.timed_out or structural_attempt.timed_out),
            gated=withheld,
            carried_forward=tuple(node.node_id for node in carried),
        )
        _log(bundle, fallback=found_a.used_fallback)
        return bundle

    def close(self) -> None:
        """Release the thread pool, if this object is the one that made it."""
        if self._owns_runner:
            self._runner.close()

    # ------------------------------------------------------------------
    # The two searches that talk to something
    # ------------------------------------------------------------------

    def _collect_carried(
        self, session: ChatSession, turn_index: int
    ) -> list[RetrievedNode]:
        """
        Whatever last turn's search found after the turn had moved on.

        Ranked below anything fresh, because it answers the question before
        this one. Dropped outright once the conversation has gone further
        than a turn past it — history about something already left behind
        pulls the assistant backwards.
        """
        arrival = session.late_arrivals.collect()
        if arrival is None:
            return []

        if arrival.is_stale(turn_index, allowed_lag=self._config.carry_forward_turns):
            logger.info(
                "a late search finally arrived, and the conversation had "
                "moved too far past it to use",
                extra={
                    "session_id": session.session_id,
                    "fetched_for_turn": arrival.turn_index,
                    "now_turn": turn_index,
                },
            )
            return []

        carried = [
            node.model_copy(
                update={"rank_score": node.rank_score * self._config.deferred_penalty}
            )
            for node in arrival.candidates
        ]
        logger.info(
            "a search from the previous turn arrived late and is being used now",
            extra={
                "session_id": session.session_id,
                "fetched_for_turn": arrival.turn_index,
                "carried": len(carried),
            },
        )
        return carried

    def _keep_for_next_turn(
        self, session: ChatSession, turn_index: int
    ) -> Callable[[str, object], None]:
        """
        Build the hook that catches a search finishing after its deadline.

        Runs on a worker thread with nobody waiting, so it does the smallest
        possible thing: puts the candidates in the day's one slot and stops.
        Everything that has to be decided about them — the sensitivity rules,
        the ranking — happens on the next turn, where the current state of the
        conversation is known.
        """

        def keep(name: str, produced: object) -> None:
            candidates = _candidates_of(produced)
            if not candidates:
                return
            session.late_arrivals.leave(
                LateArrival(turn_index=turn_index, candidates=candidates)
            )

        return keep

    def _run_searches(
        self,
        signal: RetrievalSignal,
        turn_text: str,
        session: ChatSession,
        weighting: Weighting,
    ) -> tuple[Attempt, Attempt]:
        """
        Run the meaning-based search and the anchor lookups side by side.

        One shared deadline, because eight seconds means eight seconds to
        the person waiting, not eight seconds each. Whichever does not
        finish is abandoned — but not thrown away: it goes on running, and
        what it eventually finds is kept for the next turn.
        """
        attempts = self._runner.run_all(
            {
                SEMANTIC: lambda: semantic.find_by_resemblance(
                    turn_text,
                    signal.retrieval_triggers,
                    graph=self._graph,
                    vectors=self._vectors,
                    embedder=self._embedder,
                    llm=self._llm,
                    config=self._config,
                    weighting=weighting,
                ),
                STRUCTURAL: lambda: structural.find_by_anchors(
                    signal.retrieval_triggers,
                    graph=self._graph,
                    config=self._config,
                    weighting=weighting,
                ),
            },
            timeout_seconds=self._config.retrieval_budget_seconds,
            on_late=self._keep_for_next_turn(session, signal.turn_index),
        )
        for attempt in attempts:
            if attempt.error is not None:
                logger.warning(
                    "one of the searches failed and was skipped",
                    extra={
                        "which": attempt.name,
                        "reason": type(attempt.error).__name__,
                    },
                )
        return attempts[0], attempts[1]

    # ------------------------------------------------------------------
    # Today's thread
    # ------------------------------------------------------------------

    def _remember(
        self,
        kept: list[RetrievedNode],
        *,
        session: ChatSession,
        turn_index: int,
    ) -> None:
        """
        Put this turn's keepers into today's thread and let go of the stale.

        Each new record's position in the index is fetched once, here, so
        every later turn can compare against it without another search. A
        failure to read those positions costs the next few turns their
        sharpness and nothing else — the comparison falls back to words.
        """
        buffer = session.context_buffer
        fresh = [node for node in kept if node.node_id not in buffer]
        buffer.mark_relevant(
            [node.node_id for node in kept if node.node_id in buffer],
            turn_index=turn_index,
        )
        if fresh:
            buffer.remember(
                continuity.to_entries(fresh, vectors=self._stored_vectors(fresh)),
                turn_index=turn_index,
            )
        buffer.evict_stale(turn_index)

    def _stored_vectors(self, nodes: list[RetrievedNode]) -> dict[str, list[float]]:
        """Where these records sit in the index, as far as it can say."""
        try:
            return self._vectors.get_vectors([node.node_id for node in nodes])
        except Exception as exc:  # noqa: BLE001 — a weaker thread beats a failed turn
            logger.warning(
                "could not read where these records sit in the index, so "
                "today's thread will be compared by words instead",
                extra={"reason": type(exc).__name__},
            )
            return {}

    # ------------------------------------------------------------------
    # The empty answer
    # ------------------------------------------------------------------

    def _nothing(
        self, signal: RetrievalSignal, started: float, outcome: RetrievalOutcome
    ) -> RetrievalBundle:
        """
        Answer a turn that was never going to be searched for.

        Said out loud rather than returned silently, because "there was no
        reason to look" and "there were reasons and this is not the moment"
        are different facts about the conversation and only one of them is
        about the graph.
        """
        bundle = RetrievalBundle(
            session_id=signal.session_id,
            turn_index=signal.turn_index,
            outcome=outcome,
            latency_ms=_elapsed_ms(started),
        )
        logger.info(
            "nothing was looked up for this turn",
            extra={
                "session_id": bundle.session_id,
                "turn_index": bundle.turn_index,
                "outcome": outcome.value,
            },
        )
        return bundle


# ---------------------------------------------------------------------------
# Reading the turn and reporting on it
# ---------------------------------------------------------------------------


def _candidates_of(produced: object) -> tuple:
    """
    The records inside whatever a late search handed back.

    The two searches return different shapes — one a result object, one a
    plain list — and this is the only place that has to know both, because it
    is the only place that sees a result without knowing which pass made it.
    """
    if isinstance(produced, list):
        return tuple(produced)
    candidates = getattr(produced, "candidates", None)
    return tuple(candidates) if candidates else ()


def _turn_text(session: ChatSession, turn_index: int) -> str:
    """
    What was actually said on the turn being searched for.

    Taken from the day's own memory rather than passed alongside the signal,
    because the reader has already recorded it there and two copies of one
    sentence is two chances for them to disagree.
    """
    for turn in reversed(session.recent_turns(session.turn_count)):
        if turn.turn_index == turn_index:
            return turn.content
    return ""


def _why_nothing(signal: RetrievalSignal) -> RetrievalOutcome:
    """
    Why a turn with no reasons has none.

    Two ways to arrive here and they say opposite things. An ordinary turn
    gave nothing worth looking up. A turn from somebody in acute distress
    may have given plenty, and the answer is that this is not the moment —
    which is true whether the reasons were found and discarded or the
    distress was recognised before anything was asked at all.
    """
    if (
        signal.suppressed_by_crisis
        or signal.emotional_register is EmotionalRegister.CRISIS
    ):
        return RetrievalOutcome.SUPPRESSED
    return RetrievalOutcome.NOT_NEEDED


def _keywords(signal: RetrievalSignal) -> tuple[str, ...]:
    """Every word the reasons offered, without repeats."""
    words = [word for trigger in signal.retrieval_triggers for word in trigger.keywords]
    return tuple(dict.fromkeys(words))


def _report(
    which: RetrievalPass,
    attempt: Attempt,
    found: int,
    kept: int,
    *,
    had_work: bool = True,
) -> PassReport:
    """
    What one search did, including when it did not get to do it.

    A search with nothing to do is recorded as not having run. That is not
    pedantry: it is the difference between a store that was asked and said
    nothing, and a store that was never asked — and the turn's whole account
    of itself rests on keeping those apart.
    """
    if not attempt.ok:
        return PassReport(
            which=which,
            ran=not attempt.timed_out,
            duration_ms=attempt.duration_ms,
            failure=attempt.failure,
        )
    return PassReport(
        which=which,
        ran=had_work,
        found=found,
        kept=kept,
        duration_ms=attempt.duration_ms,
    )


def _outcome(
    kept: list[RetrievedNode], reports: tuple[PassReport, ...]
) -> RetrievalOutcome:
    """
    The short version of what happened.

    A turn that found nothing because every search broke is not a turn that
    found nothing. That distinction is the one this whole layer keeps
    insisting on, because the layer above answers the two identically unless
    it is told.
    """
    if kept:
        return RetrievalOutcome.RETRIEVED
    if consulted_nothing(reports):
        return RetrievalOutcome.UNAVAILABLE
    return RetrievalOutcome.NOTHING


def _log(bundle: RetrievalBundle, *, fallback: bool) -> None:
    """
    One line per turn.

    The counts are the only warning there is for the failure that matters.
    A search that has quietly stopped returning anything breaks nothing and
    fails no test — it just makes a system built to remember behave as
    though it had never met anybody, one turn at a time.
    """
    logger.info(
        "a turn was searched for",
        extra={
            "session_id": bundle.session_id,
            "turn_index": bundle.turn_index,
            "outcome": bundle.outcome.value,
            "kept": len(bundle.candidates),
            "passes": {
                report.which.value: report.failure or report.kept
                for report in bundle.passes
            },
            "gated": len(bundle.gated),
            "within_budget": bundle.within_budget,
            "search_text_fallback": fallback,
            "latency_ms": bundle.latency_ms,
        },
    )


def _elapsed_ms(started: float) -> int:
    """How long this took, in whole milliseconds."""
    return max(int((time.perf_counter() - started) * 1000), 0)


__all__ = ["ConversationalRetriever"]
