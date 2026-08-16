"""
Feeding a written week through the real pipeline, one day at a time.

Nothing below `run_pipeline` is stood in for. The stores are real, the
stages are the shipped ones, and the only stand-ins are at the edges where a
language model would otherwise be — which is the only arrangement in which
running five days proves anything about what five real days would do.

The days run in order and each one sees what the ones before it wrote. That
is the entire point: a system that handles any single entry perfectly can
still fail to notice the same thing said three times.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from lumen.config import AppConfig
from lumen.graph.provider import GraphProvider
from lumen.operational.repositories import OperationalStore
from lumen.operational.schemas import BufferMessageRecord
from lumen.pipeline.orchestration import run_pipeline
from lumen.providers.fake import FakeLLMProvider
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.schemas.enums import ModelRole
from lumen.schemas.pipeline import BufferMessage, RunReport, SessionDecayEvent
from lumen.simulation.corpus import CORPUS, THEMES, SimulatedDay, replies_for
from lumen.simulation.themes import ThemedEmbeddingProvider
from lumen.vector.provider import VectorProvider

logger = logging.getLogger(__name__)

# Which step of the pipeline each prompt belongs to, spotted by a phrase
# unique to that prompt. Needed because one dictionary keyed by prompt
# cannot tell day one's reading prompt from day three's — they are the same
# prompt with different writing inside it.
STEP_MARKERS: tuple[tuple[str, str], ...] = (
    ("normalize_voice", "TRANSCRIPT:"),
    ("normalize_text", "Below is a journal entry someone typed"),
    ("structure", "SPLITTING"),
    ("triage", "EPISODES:"),
    ("reflection_prompts", "too short to analyse properly"),
    ("extract_reflection", "FINDINGS (observations)"),
    ("extract_raw_capture", "Below is a short or unclear journal entry"),
    ("correction", "Some of what you returned could not be used"),
    ("hyde", "write a single sentence as it might appear"),
    ("escalation", "A faster model read these items"),
    ("decision", "Below are things someone noticed in one journal entry"),
)


class CorpusScript:
    """
    Answers any step of whichever day is currently being processed.

    The step being asked for is worked out from a phrase unique to that
    stage's prompt. Which *day* it belongs to is not worked out at all — it
    is announced, because the runner feeds the days one at a time and
    already knows.

    That is a deliberate simplification over reading the day out of the
    prompt. Only the first two stages quote what the person wrote; by the
    time the search and decision prompts are built, what they contain is
    what the earlier models said, and matching a day against that would mean
    the fixture had to predict its own output.

    A step the current day has no answer for raises rather than falling back
    to something plausible. A stand-in that invents an answer lets a test
    keep passing after the thing it was checking has changed.
    """

    def __init__(self, days: Sequence[SimulatedDay]) -> None:
        self.replies = {day.day: replies_for(day) for day in days}
        self.current: int | None = None
        self.asked: list[tuple[int, str]] = []

    def begin(self, day: SimulatedDay) -> None:
        """Say which day the prompts from here on belong to."""
        self.current = day.day

    def __call__(self, prompt: str) -> str:
        if self.current is None:
            raise RuntimeError(
                "a prompt arrived before any day had begun; the runner sets "
                "the day before each entry is processed"
            )

        step = self._step_of(prompt)
        answers = self.replies[self.current]
        if step not in answers:
            raise KeyError(
                f"day {self.current} has no reply for the {step!r} step; "
                f"it answers {sorted(answers)}"
            )

        self.asked.append((self.current, step))
        return answers[step]

    def steps_asked_on(self, day: int) -> list[str]:
        """Which steps ran for one day, in order. Useful when a day misbehaves."""
        return [step for asked_day, step in self.asked if asked_day == day]

    def _step_of(self, prompt: str) -> str:
        for step, marker in STEP_MARKERS:
            if marker in prompt:
                return step
        raise KeyError(f"no known pipeline step matches this prompt: {prompt[:120]!r}")


def build_models(
    days: Sequence[SimulatedDay] = CORPUS,
) -> tuple[CorpusScript, LLMProvider, LLMProvider]:
    """
    A pair of stand-in models that can answer every step of every day.

    Both share one script, so nothing has to know which of the two a given
    step happens to use, and the script is handed back as well because the
    runner has to tell it which day is starting.
    """
    script = CorpusScript(days)
    return (
        script,
        FakeLLMProvider(script, role=ModelRole.LIGHTWEIGHT, model="fake-light"),
        FakeLLMProvider(script, role=ModelRole.THINKING, model="fake-thinker"),
    )


def build_embedder(dimensions: int = 768) -> ThemedEmbeddingProvider:
    """A stand-in embedder that knows the corpus's themes."""
    return ThemedEmbeddingProvider(THEMES, dimensions=dimensions)


def simulate_days(
    days: Sequence[SimulatedDay] = CORPUS,
    *,
    graph: GraphProvider,
    vectors: VectorProvider,
    ops: OperationalStore,
    embedder: EmbeddingProvider | None = None,
    models: tuple[LLMProvider, LLMProvider] | None = None,
    config: AppConfig | None = None,
    user_id: str = "local",
) -> list[RunReport]:
    """
    Run a written week through the pipeline, in order, into real stores.

    Returns one report per day. Every day is run even if an earlier one
    failed — a run that stops at the first problem hides how far the damage
    spread, and the whole question here is what accumulates.
    """
    settings = config or AppConfig()
    script, lightweight, thinking = (
        (None, *models) if models else build_models(days)
    )
    embed = embedder or build_embedder(settings.vector.vector_size)

    reports: list[RunReport] = []
    for day in days:
        if script is not None:
            script.begin(day)
        event = _arrive(day, ops=ops, user_id=user_id)
        report = run_pipeline(
            event,
            graph=graph,
            vectors=vectors,
            embedder=embed,
            lightweight=lightweight,
            thinking=thinking,
            ops=ops,
            config=settings,
        )
        reports.append(report)
        logger.info(
            "simulated day complete",
            extra={
                "day": day.day,
                "event_date": day.event_date.isoformat(),
                "records": report.nodes_written,
                "status": report.job_status,
            },
        )

    return reports


def _arrive(
    day: SimulatedDay, *, ops: OperationalStore, user_id: str
) -> SessionDecayEvent:
    """
    Put one day's writing into the waiting room and call it finished.

    The same two steps a real entry goes through before the pipeline sees
    it: a conversation is opened and written to, and then it goes quiet.
    """
    written_at = datetime.combine(day.event_date, datetime.min.time(), tzinfo=UTC).replace(
        hour=21
    )

    buffer = ops.buffers.find_or_create(
        user_id=user_id, event_date=day.event_date, session_label="A"
    )
    ops.buffers.append_message(
        buffer.session_id,
        BufferMessageRecord(
            message_id=f"msg_day_{day.day}",
            session_id=buffer.session_id,
            seq=0,
            role="USER",
            content=day.text,
            timestamp=written_at,
            event_date=day.event_date,
        ),
    )

    return SessionDecayEvent(
        session_id=buffer.session_id,
        user_id=user_id,
        event_date=day.event_date,
        session_label="A",
        message_count=1,
        raw_buffer=[
            BufferMessage(
                message_id=f"msg_day_{day.day}",
                role="USER",
                content=day.text,
                timestamp=written_at,
                event_date=day.event_date,
            )
        ],
        triggered_at=written_at.replace(hour=23),
    )


__all__ = [
    "CorpusScript",
    "STEP_MARKERS",
    "simulate_days",
    "build_models",
    "build_embedder",
]
