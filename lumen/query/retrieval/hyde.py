"""
Writing the text a turn is actually searched with.

Two steps: ask the model to invent, for each reason the turn gave, the
journal entry that would have answered it, then turn those inventions into
vectors in one batch.

Both steps can fail, and they fail differently. A model that will not answer
costs quality — the search falls back to the person's own words, which is a
blunter search but a real one. An embedding that will not run costs the
search itself, and that is reported rather than absorbed, because a search
that did not happen and a search that found nothing look identical from the
outside and mean opposite things.

Answers are matched back to reasons **by number**, and a short answer is
padded rather than shifted up. Searching one reason with another reason's
text does not fail — it returns confident, wrong records, which is worse
than returning none.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import ValidationError

from lumen.providers.errors import ProviderError
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.query.retrieval.contracts import HydeReply, SearchText
from lumen.query.retrieval.prompts import (
    SYSTEM_INSTRUCTION,
    build_prompt,
    own_words,
)
from lumen.schemas.enums import EmbeddingTaskType
from lumen.schemas.query import RetrievalTrigger

logger = logging.getLogger(__name__)


def write_search_text(
    turn_text: str,
    triggers: Sequence[RetrievalTrigger],
    *,
    provider: LLMProvider,
) -> SearchText:
    """
    Ask for one invented record per reason, in a single call.

    Falling back to the person's own words is the safe failure. It searches
    worse; it still searches.
    """
    if not triggers:
        return SearchText()

    spoken = tuple(own_words(turn_text, trigger) for trigger in triggers)
    reply = _request(turn_text, triggers, provider=provider)
    if reply is None:
        return SearchText(texts=spoken, used_fallback=True)

    return SearchText(texts=_align(reply, spoken))


def to_vectors(
    text: SearchText, *, embedder: EmbeddingProvider
) -> tuple[list[list[float]], bool]:
    """
    Turn every search text into a vector in one batch.

    Embedded as documents rather than as questions. Turning a question into
    a document is exactly what inventing the record was for, and labelling
    it a question would apply that same correction twice.

    Returns the vectors and whether the embedding failed, because the caller
    has to be able to say "nothing could be looked up" rather than "nothing
    was found".
    """
    if not text.texts:
        return [], False

    try:
        vectors = embedder.embed_batch(
            list(text.texts), task_type=EmbeddingTaskType.DOCUMENT
        )
    except ProviderError as exc:
        logger.warning(
            "could not turn the search text into vectors, so nothing was searched",
            extra={"reason": type(exc).__name__, "wanted": len(text.texts)},
        )
        return [], True

    if len(vectors) != len(text.texts):
        # Position is the only thing tying a vector to its reason, so a batch
        # that comes back a different length cannot be trusted to line up.
        logger.warning(
            "the embedder returned a different number of vectors than asked for",
            extra={"asked": len(text.texts), "got": len(vectors)},
        )
        return [], True

    return vectors, False


def _request(
    turn_text: str, triggers: Sequence[RetrievalTrigger], *, provider: LLMProvider
) -> HydeReply | None:
    """Ask the model once, and hand back nothing rather than raising."""
    try:
        result = provider.generate_structured(
            build_prompt(turn_text, triggers),
            HydeReply,
            system_instruction=SYSTEM_INSTRUCTION,
        )
    except ProviderError as exc:
        _log_fallback("provider_error", type(exc).__name__)
        return None

    if result.data is None:
        _log_fallback("unreadable_reply", result.parse_error)
        return None

    try:
        return HydeReply.model_validate(result.data)
    except ValidationError as exc:
        _log_fallback("unexpected_shape", f"{exc.error_count()} field errors")
        return None


def _align(reply: HydeReply, spoken: tuple[str, ...]) -> tuple[str, ...]:
    """
    Put each invented record beside the reason it belongs to.

    Placed by the number it came back with, not by the order it arrived in,
    and anything missing keeps the person's own words. Sliding the rest up
    to fill a gap would search every later reason with the wrong text.
    """
    by_position = {
        item.index: item.text.strip()
        for item in reply.hypotheticals
        if item.text.strip()
    }
    filled = tuple(
        by_position.get(position, spoken[position - 1])
        for position in range(1, len(spoken) + 1)
    )
    missing = len(spoken) - sum(1 for position in range(1, len(spoken) + 1) if position in by_position)
    if missing > 0:
        logger.info(
            "some reasons are being searched with the turn's own words",
            extra={"missing": missing, "reasons": len(spoken)},
        )
    return filled


def _log_fallback(reason: str, detail: str | None = None) -> None:
    """Record that the search text had to be written without the model."""
    logger.warning(
        "could not invent search text, falling back to what was actually said",
        extra={"reason": reason, "detail": detail},
    )


__all__ = ["write_search_text", "to_vectors"]
