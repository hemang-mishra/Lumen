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

from pydantic import ValidationError

from lumen.config import QueryConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.providers.errors import ProviderError
from lumen.providers.protocols import LLMProvider
from lumen.query.formulation import safety, triage
from lumen.query.formulation.contracts import ClassifierReply
from lumen.query.formulation.deadline import DeadlineExceeded, DeadlineRunner
from lumen.query.formulation.grounding import (
    GroundingContext,
    clean_names,
    era_vocabulary,
    ground_triggers,
    parse_domain,
)
from lumen.query.formulation.prompts import SYSTEM_INSTRUCTION, build_prompt
from lumen.query.session import ChatSession
from lumen.schemas.enums import EmotionalRegister, FormulationPath
from lumen.schemas.query import ChatTurn, RetrievalSignal, RetrievalTrigger

logger = logging.getLogger(__name__)


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
        graph: ReadOnlyGraph,
        config: QueryConfig | None = None,
        runner: DeadlineRunner | None = None,
    ) -> None:
        self._llm = llm
        self._graph = graph
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

        reply, path = self._ask(turn, session)
        if reply is None:
            return self._finish(turn, session, started=started, path=path)

        return self._read(reply, turn, session, started=started)

    def close(self) -> None:
        """Release the thread pool, if this object is the one that made it."""
        if self._owns_runner:
            self._runner.close()

    # ------------------------------------------------------------------
    # Asking the model
    # ------------------------------------------------------------------

    def _ask(
        self, turn: ChatTurn, session: ChatSession
    ) -> tuple[ClassifierReply | None, FormulationPath]:
        """
        One call, with a hard limit on how long the turn may wait for it.

        Every way this can go wrong ends the same way — nothing is looked
        up — but they are reported separately, because a model that is
        always slow and a model that is always erroring are different
        problems with different fixes.
        """
        eras = era_vocabulary(self._graph, session, config=self._config)
        history = session.recent_turns(
            max(self._config.formulation_context_turns - 1, 0)
        )
        prompt = build_prompt(
            [*history, turn],
            classify_index=turn.turn_index,
            eras=eras,
            keyword_limit=self._config.max_keywords_per_trigger,
        )

        try:
            result = self._runner.run(
                lambda: self._llm.generate_structured(
                    prompt, ClassifierReply, system_instruction=SYSTEM_INSTRUCTION
                ),
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

        if result.data is None:
            logger.warning(
                "the model's reading of the turn could not be read back",
                extra={
                    "session_id": session.session_id,
                    "reason": result.parse_error,
                },
            )
            return None, FormulationPath.CALL_FAILED

        try:
            return ClassifierReply.model_validate(result.data), FormulationPath.CLASSIFIED
        except ValidationError as exc:
            logger.warning(
                "the model's reading of the turn had an unexpected shape",
                extra={
                    "session_id": session.session_id,
                    "errors": exc.error_count(),
                },
            )
            return None, FormulationPath.CALL_FAILED

    # ------------------------------------------------------------------
    # Making sense of what came back
    # ------------------------------------------------------------------

    def _read(
        self,
        reply: ClassifierReply,
        turn: ChatTurn,
        session: ChatSession,
        *,
        started: float,
    ) -> RetrievalSignal:
        """
        Turn a model's answer into a checked signal.

        The order matters at the end. Anything the person opened up is
        remembered *before* a crisis clears the reasons to search, because
        they did open it and tomorrow's reading should not have to discover
        that again — the crisis suppresses this turn's lookup, not the fact
        that the subject is now on the table.
        """
        context = GroundingContext(
            graph=self._graph,
            eras=era_vocabulary(self._graph, session, config=self._config),
            keyword_limit=self._config.max_keywords_per_trigger,
        )
        triggers = ground_triggers(reply.triggers, context=context)[
            : max(self._config.max_triggers_per_turn, 0)
        ]

        opened = parse_domain(reply.critical_domain_opened)
        if opened is not None:
            session.unlock(opened)

        register = _parse_register(reply.emotional_register)
        suppressed = register is EmotionalRegister.CRISIS and bool(triggers)
        if register is EmotionalRegister.CRISIS:
            triggers = ()

        return self._finish(
            turn,
            session,
            started=started,
            path=FormulationPath.CLASSIFIED,
            register=register,
            confidence=_clamp(reply.confidence),
            triggers=triggers,
            names=clean_names(reply.named_entities),
            opened=opened,
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


__all__ = ["QueryFormulator"]
