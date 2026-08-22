"""
Reading one turn of a live conversation.

This runs on every single thing the person says, and its whole job is to
answer one question cheaply: is anything in this person's recorded history
worth going and finding before the AI replies?

Saying yes too easily makes the conversation stutter — every "yeah, go on"
would buy a pause while something is searched for. Saying no too easily
makes a system built to remember behave as though it had never met them. So
the answer is worked out in a fixed order, cheapest and most certain first:

  1. Does the turn trip the distress floor? Then nothing, and no model call.
  2. Is it a plain acknowledgement? Then nothing, and no model call.
  3. Otherwise ask the model, under a deadline it may not exceed.
  4. Check everything it claimed against the graph, and drop what is not there.
  5. Keep the best few reasons, remember anything the person opened up, and
     throw the lot away if they turn out to be in crisis.

Nothing here writes to anything. The graph is handed over as a reader, and
the only thing that changes is the day's own memory of itself.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from pydantic import ValidationError

from lumen.config import QueryConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.providers.errors import ProviderError
from lumen.providers.protocols import LLMProvider
from lumen.query.formulation import safety, triage
from lumen.query.formulation.contracts import ClassifierReply
from lumen.query.deadline import DeadlineExceeded, DeadlineRunner
from lumen.query.formulation.grounding import (
    GroundingContext,
    clean_names,
    era_vocabulary,
    ground_triggers,
    parse_domain,
)
from lumen.query.formulation.prompts import SYSTEM_INSTRUCTION, build_prompt
from lumen.query.session import ChatSession
from lumen.stores import StoreRegistry
from lumen.schemas.enums import Domain, EmotionalRegister, FormulationPath
from lumen.schemas.query import ChatTurn, RetrievalSignal, RetrievalTrigger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reading:
    """
    What one deadline-guarded reading of a turn produced.

    Exists so the work and its consequences can be separated. Everything here
    was worked out on a worker thread that may have been abandoned; nothing
    is acted on until the calling thread decides the reading arrived in time.

    Attributes:
        register: How the person sounds.
        confidence: How sure the model was, held to 0–1.
        triggers: The reasons to search that survived being checked against
            the graph.
        names: The names the turn mentioned, tidied but not checked.
        opened: A sensitive area of life the person opened themselves, if any.
        eras_to_remember: The era names, when they were fetched on this turn
            and the fetch worked. Nothing when they were already known, and
            nothing when the read failed — a failed read must not be cached,
            or one bad moment switches off era lookups until midnight.
    """

    register: EmotionalRegister
    confidence: float
    triggers: tuple[RetrievalTrigger, ...]
    names: tuple[str, ...]
    opened: Domain | None
    eras_to_remember: tuple[str, ...] | None


class QueryFormulator:
    """
    Reads live turns, one at a time.

    Built as an object rather than a plain function because it owns
    something with a lifetime: the small pool of threads used to stop
    waiting on a slow model. Everything else it needs is handed in, so a
    test can point it at a scripted model and a temporary graph without
    changing anything about how it works.
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        stores: StoreRegistry,
        config: QueryConfig | None = None,
        runner: DeadlineRunner | None = None,
    ) -> None:
        self._llm = llm
        self._stores = stores
        self._config = config or QueryConfig()
        self._owns_runner = runner is None
        self._runner = runner or DeadlineRunner(
            max_workers=self._config.formulation_max_workers
        )

    def formulate(self, turn: ChatTurn, session: ChatSession) -> RetrievalSignal:
        """
        Read one turn and say what, if anything, is worth looking up.

        The turn is added to the day's memory whichever way the reading goes,
        including when nothing is looked up. A turn that was skipped was
        still said, and the turn after it may only make sense against it.
        """
        started = time.perf_counter()

        if safety.in_crisis(turn.content):
            return self._finish(
                turn,
                session,
                started=started,
                path=FormulationPath.SAFETY_FLOOR,
                register=EmotionalRegister.CRISIS,
                confidence=1.0,
            )

        if triage.is_trivial(turn.content):
            return self._finish(
                turn,
                session,
                started=started,
                path=FormulationPath.ACKNOWLEDGEMENT,
                register=EmotionalRegister.STABLE,
                confidence=1.0,
            )

        reading, path = self._consider(turn, session)
        if reading is None:
            return self._finish(turn, session, started=started, path=path)

        return self._read(reading, turn, session, started=started)

    def close(self) -> None:
        """Release the thread pool, if this object is the one that made it."""
        if self._owns_runner:
            self._runner.close()

    # ------------------------------------------------------------------
    # Asking the model
    # ------------------------------------------------------------------

    def _consider(
        self, turn: ChatTurn, session: ChatSession
    ) -> tuple[Reading | None, FormulationPath]:
        """
        Read the turn, with a hard limit on how long it may take.

        **Everything that touches a store or a model is inside the limit**,
        not just the model call. The graph reads on either side of it — the
        era names before, the checks on what the model claimed after — are
        reads of an embedded database that serialises against the importer,
        so one of them can wait on a write transaction for as long as that
        transaction takes. A budget covering only the middle of the stage is
        a budget that describes nothing.

        Nothing here touches the day's session. It is read once, before the
        work starts, and written to afterwards by the caller — because a
        reading that misses its deadline goes on running with nobody waiting
        for it, and a stray write from one of those would let a turn nobody
        used unlock a sensitive subject.

        Every way this can go wrong ends the same way — nothing is looked
        up — but they are reported separately, because a model that is
        always slow and a model that is always erroring are different
        problems with different fixes.
        """
        known = session.era_vocabulary
        history = session.recent_turns(
            max(self._config.formulation_context_turns - 1, 0)
        )

        try:
            reading = self._runner.run(
                lambda: self._reading(turn, history, known, session.user_id),
                timeout_seconds=self._config.formulation_timeout_seconds,
            )
        except DeadlineExceeded:
            logger.info(
                "the turn moved on before the model answered, so nothing was looked up",
                extra={
                    "session_id": session.session_id,
                    "turn_index": turn.turn_index,
                    "budget_seconds": self._config.formulation_timeout_seconds,
                },
            )
            return None, FormulationPath.TIMED_OUT
        except ProviderError as exc:
            logger.warning(
                "the model could not read the turn, so nothing was looked up",
                extra={
                    "session_id": session.session_id,
                    "reason": type(exc).__name__,
                },
            )
            return None, FormulationPath.CALL_FAILED

        if reading is None:
            return None, FormulationPath.CALL_FAILED
        return reading, FormulationPath.CLASSIFIED

    def _reading(
        self,
        turn: ChatTurn,
        history: list[ChatTurn],
        known_eras: tuple[str, ...] | None,
        user_id: str,
    ) -> Reading | None:
        """
        The whole of the work, on one thread, inside one budget.

        Runs on a worker thread and therefore touches nothing shared. The era
        names it may have to fetch are handed back rather than cached here,
        so that only a reading the turn actually used is remembered for the
        rest of the day.

        The graph is borrowed for the length of this rather than held, since
        which graph it is depends on who is talking — and the whole point of
        a store per person is that this object cannot know that in advance.
        """
        with self._stores.lease(user_id) as stores:
            return self._read_against(turn, history, known_eras, stores.graph)

    def _read_against(
        self,
        turn: ChatTurn,
        history: list[ChatTurn],
        known_eras: tuple[str, ...] | None,
        graph: ReadOnlyGraph,
    ) -> Reading | None:
        """The reading itself, against one person's graph."""
        fetched = (
            era_vocabulary(graph, config=self._config)
            if known_eras is None
            else None
        )
        eras = known_eras if known_eras is not None else fetched

        prompt = build_prompt(
            [*history, turn],
            classify_index=turn.turn_index,
            eras=eras or (),
            keyword_limit=self._config.max_keywords_per_trigger,
        )
        result = self._llm.generate_structured(
            prompt, ClassifierReply, system_instruction=SYSTEM_INSTRUCTION
        )

        if result.data is None:
            logger.warning(
                "the model's reading of the turn could not be read back",
                extra={"reason": result.parse_error},
            )
            return None

        try:
            reply = ClassifierReply.model_validate(result.data)
        except ValidationError as exc:
            logger.warning(
                "the model's reading of the turn had an unexpected shape",
                extra={"errors": exc.error_count()},
            )
            return None

        context = GroundingContext(
            graph=graph,
            eras=eras or (),
            keyword_limit=self._config.max_keywords_per_trigger,
        )
        triggers = ground_triggers(reply.triggers, context=context)[
            : max(self._config.max_triggers_per_turn, 0)
        ]

        return Reading(
            register=_parse_register(reply.emotional_register),
            confidence=_clamp(reply.confidence),
            triggers=triggers,
            names=clean_names(reply.named_entities),
            opened=parse_domain(reply.critical_domain_opened),
            # Only a freshly fetched, successful answer is worth keeping. A
            # failed read comes back as nothing and is not remembered, so the
            # next turn asks again instead of assuming this history has no
            # eras in it for the rest of the day.
            eras_to_remember=fetched,
        )

    # ------------------------------------------------------------------
    # Making sense of what came back
    # ------------------------------------------------------------------

    def _read(
        self,
        reading: Reading,
        turn: ChatTurn,
        session: ChatSession,
        *,
        started: float,
    ) -> RetrievalSignal:
        """
        Apply a finished reading to the day, and build the signal from it.

        This is the only place the day is changed, and it runs on the calling
        thread — so a reading that was abandoned for missing its deadline can
        never reach it, however long it goes on running afterwards.

        The order matters at the end. Anything the person opened up is
        remembered *before* a crisis clears the reasons to search, because
        they did open it and tomorrow's reading should not have to discover
        that again — the crisis suppresses this turn's lookup, not the fact
        that the subject is now on the table.
        """
        if reading.eras_to_remember is not None:
            session.remember_era_vocabulary(reading.eras_to_remember)

        if reading.opened is not None:
            session.unlock(reading.opened)

        triggers = reading.triggers
        in_crisis = reading.register is EmotionalRegister.CRISIS
        suppressed = in_crisis and bool(triggers)
        if in_crisis:
            triggers = ()

        return self._finish(
            turn,
            session,
            started=started,
            path=FormulationPath.CLASSIFIED,
            register=reading.register,
            confidence=reading.confidence,
            triggers=triggers,
            names=reading.names,
            opened=reading.opened,
            suppressed=suppressed,
        )

    def _finish(
        self,
        turn: ChatTurn,
        session: ChatSession,
        *,
        started: float,
        path: FormulationPath,
        register: EmotionalRegister = EmotionalRegister.STABLE,
        confidence: float = 0.0,
        triggers: tuple[RetrievalTrigger, ...] = (),
        names: tuple[str, ...] = (),
        opened=None,
        suppressed: bool = False,
    ) -> RetrievalSignal:
        """
        Build the signal, remember the turn, and record what happened.

        One log line per turn, and it is the only way anyone will notice the
        failure that matters most: a reading that quietly says "nothing to
        look up" to everything would show up here as a run of empty triggers
        and nowhere else.
        """
        signal = RetrievalSignal(
            session_id=session.session_id,
            turn_index=turn.turn_index,
            retrieval_triggers=triggers,
            named_entities_mentioned=names,
            emotional_register=register,
            query_formulation_confidence=confidence,
            critical_domain_opened=opened,
            unlocked_domains=session.unlocked_domains,
            formulation_path=path,
            latency_ms=_elapsed_ms(started),
            suppressed_by_crisis=suppressed,
        )
        session.record_turn(turn)

        logger.info(
            "a turn was read",
            extra={
                "session_id": signal.session_id,
                "turn_index": signal.turn_index,
                "path": signal.formulation_path.value,
                "register": signal.emotional_register.value,
                "triggers": [kind.value for kind in signal.trigger_types],
                "latency_ms": signal.latency_ms,
            },
        )
        return signal


def _parse_register(value: str) -> EmotionalRegister:
    """
    How the person sounds, from whatever word the model used.

    An answer that names none of the four is read as ordinary conversation.
    That is the middle option: it neither suppresses a lookup that should
    happen nor unlocks the aggressive one.
    """
    wanted = value.strip().upper()
    return next(
        (
            register
            for register in EmotionalRegister
            if register.value == wanted
        ),
        EmotionalRegister.STABLE,
    )


def _clamp(confidence: float) -> float:
    """Confidence held to the range it is defined over."""
    return min(max(float(confidence), 0.0), 1.0)


def _elapsed_ms(started: float) -> int:
    """How long the reading took, in whole milliseconds."""
    return max(int((time.perf_counter() - started) * 1000), 0)


__all__ = ["QueryFormulator", "Reading"]
