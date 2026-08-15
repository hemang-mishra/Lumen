"""
The stage that turns writing into structure.

One episode comes in and a set of typed nodes goes out: the things the
person noticed, what happened to them, and the sequences running between
the two.

The defining constraint is what this stage is not given. It never sees
what the person has believed before, what patterns they have shown, or
anything they wrote last month. That is deliberate and it is the whole
design. A model shown the existing list of someone's patterns stops
reading the entry and starts matching against the list — a genuinely new
experience gets filed under the nearest old one, and real change becomes
invisible exactly when it matters. Comparing today against the past is a
later step's job, done once today has already been read on its own terms.

Nothing here touches a database. The models it needs are handed to it, so
the whole stage can be run with nothing installed and its behaviour
checked exactly.
"""

from __future__ import annotations

import logging
import time

from lumen.config import AppConfig
from lumen.pipeline.extraction import passes, retry
from lumen.pipeline.extraction.contracts import DropRule, ExtractionOutcome
from lumen.providers.protocols import LLMProvider
from lumen.schemas.enums import EntryClass
from lumen.schemas.pipeline import ExtractionResult, MicroextractionInput

logger = logging.getLogger(__name__)


def extract(
    payload: MicroextractionInput,
    *,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    config: AppConfig | None = None,
) -> ExtractionResult:
    """
    Read one episode and return what is in it.

    Both models are asked for even though any one episode uses only one of
    them, because which one is used is decided in here, from how much the
    episode earned in the previous stage. A caller should not have to know
    that rule in order to call this function, and should not be able to get
    it wrong.

    Nothing is invented to cover a failure. If the reading fails, the
    episode yields nothing and says so. The person's writing is already
    safely stored, so a failed reading costs an analysis that can be run
    again; a filled-in one would cost the truth of their history, and
    nothing downstream would ever be able to tell.

    A close reading that comes back partly unusable is asked about again
    before it is given up on. A thin entry is not: it is one cheap call
    with almost nothing in it to get wrong, and the one rule it can break
    is the one rule that must never be re-asked.
    """
    started = time.perf_counter()
    limits = (config or AppConfig()).pipeline
    is_thin = payload.episode.entry_class is EntryClass.RAW_CAPTURE

    if is_thin:
        outcome = passes.read_raw_capture(
            payload, provider=lightweight, limits=limits
        )
        model_name = lightweight.model_name
    else:
        outcome = retry.read_with_corrections(
            payload, provider=thinking, limits=limits
        )
        model_name = thinking.model_name

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _log_outcome(payload, outcome, thin=is_thin, duration_ms=elapsed_ms)

    return ExtractionResult(
        episode_id=payload.episode.episode_id,
        observations=list(outcome.observations),
        events=list(outcome.events),
        sessions=list(outcome.sessions),
        causal_chains=list(outcome.chains),
        causal_steps=list(outcome.steps),
        failed_observations=list(outcome.failed),
        extraction_model=model_name,
        validation_passed=_is_trustworthy(outcome),
        retry_count=outcome.attempts - 1,
        read_failed=outcome.used_fallback,
    )


def _is_trustworthy(outcome: ExtractionOutcome) -> bool:
    """
    Decide whether this reading can be relied on as complete.

    Three things make it not. The reading may have failed outright. It may
    have given up on something — an item nobody could correct, or one
    trimmed off for being over the limit. Or it may have come back clean
    and empty, which for an entry judged worth reading closely means the
    reading did not work, whatever the model reported.

    A stumble that was recovered does not count against it. An item refused
    on the first attempt and accepted on the second cost nothing in the
    end, and reporting the run as untrustworthy because of a mistake that
    was fixed would make the flag useless for telling real losses apart.
    """
    if outcome.used_fallback or outcome.abandoned or _was_truncated(outcome):
        return False
    return not outcome.is_empty


def _was_truncated(outcome: ExtractionOutcome) -> bool:
    """
    True when something was cut for being over a ceiling.

    Never re-asked, since nothing was wrong with the items themselves, but
    still a real loss and so still worth reporting as one.
    """
    return any(record.rule is DropRule.OVER_LIMIT for record in outcome.drops)


def _log_outcome(
    payload: MicroextractionInput,
    outcome: ExtractionOutcome,
    *,
    thin: bool,
    duration_ms: int,
) -> None:
    """
    Record one line about what the episode produced.

    Counts and reasons only, never a word of the writing itself, so the log
    does not slowly become a second copy of someone's private history.

    The counts are the only warning available for the quiet failure this
    stage is prone to. A prompt that drifts, or a model that starts
    returning two findings where it used to return nine, breaks nothing and
    fails no test — it just gradually produces a thinner and thinner record
    of somebody's life.
    """
    logger.info(
        "extraction complete",
        extra={
            "episode_id": payload.episode.episode_id,
            "entry_id": payload.entry_id,
            "entry_class": payload.episode.entry_class.value,
            "light_path": thin,
            "observations": len(outcome.observations),
            "events": len(outcome.events),
            "causal_chains": len(outcome.chains),
            "causal_steps": len(outcome.steps),
            "anchored": bool(outcome.sessions),
            "dropped": len(outcome.drops),
            "drop_reasons": sorted({record.rule.value for record in outcome.drops}),
            "failed": len(outcome.failed),
            "ungrounded": outcome.ungrounded,
            "attempts": outcome.attempts,
            "read_failed": outcome.used_fallback,
            "duration_ms": duration_ms,
        },
    )


__all__ = ["extract"]
