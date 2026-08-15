"""
Writing the text that the search is actually run with.

Searching a personal history with the words of a fresh observation works
badly, because the thing being looked for and the thing that would match it
are written completely differently. The trick used here is to write a
plausible version of the answer first and search with that instead.

Two things about how it is done matter more than the trick itself.

It happens **once for the whole episode**. A rich entry produces twenty or
more findings, and a call each would make this the most expensive step in
the pipeline by a wide margin. One call handles them all and one batch turns
them into vectors.

The answers are matched back to the findings **by position**, and a short
answer is padded rather than shifted up. A search run with another node's
text does not fail — it returns confident, wrong matches, which is worse
than returning none.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from lumen.pipeline.retrieval.contracts import HydeResponse, HydeResult, SearchTarget
from lumen.pipeline.retrieval.prompts import (
    HYDE_PROMPT,
    SYSTEM_INSTRUCTION,
    render_targets,
)
from lumen.providers.errors import ProviderError
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.schemas.enums import EmbeddingTaskType

logger = logging.getLogger(__name__)


def write_search_text(
    targets: tuple[SearchTarget, ...], *, provider: LLMProvider
) -> HydeResult:
    """
    Ask for one made-up historical record per finding, in a single call.

    If the call fails, every finding is searched for using its own words.
    That is a worse search but a real one — and the alternative, returning
    nothing, would look exactly like a person having no history on the
    subject, which is the one mistake this stage must not make.
    """
    if not targets:
        return HydeResult()

    own_words = tuple(target.text for target in targets)
    response = _request(targets, provider=provider)
    if response is None:
        return HydeResult(texts=own_words, used_fallback=True)

    return HydeResult(texts=_align(response, own_words))


def to_vectors(
    result: HydeResult, *, embedder: EmbeddingProvider
) -> tuple[list[list[float]], bool]:
    """
    Turn every search text into a vector in one batch.

    Embedded as documents rather than as queries. Turning a question into a
    document is the whole point of writing the made-up record, and labelling
    it a query would apply that same correction a second time.

    Returns the vectors and whether the embedding failed. A failure means
    there is no search to run at all, which the caller has to be able to
    tell apart from a search that found nothing.
    """
    if not result.texts:
        return [], False

    try:
        vectors = embedder.embed_batch(
            list(result.texts), task_type=EmbeddingTaskType.DOCUMENT
        )
    except ProviderError as exc:
        logger.warning(
            "could not turn search text into vectors, so no search was run",
            extra={"reason": type(exc).__name__, "target_count": len(result.texts)},
        )
        return [], True

    if len(vectors) != len(result.texts):
        # Order is the only thing tying a vector to its finding, so a batch
        # that comes back a different length cannot be trusted to line up.
        logger.warning(
            "embedding returned a different number of vectors than asked for",
            extra={"asked": len(result.texts), "got": len(vectors)},
        )
        return [], True

    return vectors, False


def _request(
    targets: tuple[SearchTarget, ...], *, provider: LLMProvider
) -> HydeResponse | None:
    """Ask the model once, and hand back nothing rather than raising."""
    prompt = HYDE_PROMPT.format(
        items=render_targets([target.text for target in targets])
    )
    try:
        result = provider.generate_structured(
            prompt, HydeResponse, system_instruction=SYSTEM_INSTRUCTION
        )
    except ProviderError as exc:
        _log_fallback("provider_error", type(exc).__name__)
        return None

    if result.data is None:
        _log_fallback("unparseable_response", result.parse_error)
        return None

    try:
        return HydeResponse.model_validate(result.data)
    except ValidationError as exc:
        _log_fallback("unexpected_shape", f"{exc.error_count()} field errors")
        return None


def _align(response: HydeResponse, own_words: tuple[str, ...]) -> tuple[str, ...]:
    """
    Put each answer beside the finding it belongs to.

    Answers are placed by the number they came back with, not by the order
    they arrived in, and anything missing keeps the finding's own words.
    Sliding the remaining answers up to fill a gap would search for every
    later finding using the wrong text — and unlike a missing answer, that
    failure is invisible.
    """
    by_position = {
        item.index: item.text.strip()
        for item in response.hypotheticals
        if item.text.strip()
    }
    filled = [
        by_position.get(position, own_words[position - 1])
        for position in range(1, len(own_words) + 1)
    ]
    missing = len(own_words) - len(by_position)
    if missing > 0:
        logger.info(
            "some findings are being searched for with their own words",
            extra={"missing": missing, "target_count": len(own_words)},
        )
    return tuple(filled)


def _log_fallback(reason: str, detail: str | None = None) -> None:
    """Record that search text had to be written without the model."""
    logger.warning(
        "could not write search text, falling back to the findings' own words",
        extra={"reason": reason, "detail": detail},
    )


__all__ = ["write_search_text", "to_vectors"]
