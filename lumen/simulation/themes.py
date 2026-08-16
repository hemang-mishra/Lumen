"""
A stand-in embedder that can tell two entries are about the same thing.

The ordinary stand-in turns text into a hash. Two sentences about the same
struggle land as far apart as two sentences about nothing in common, which
is fine for testing how a fixed set of candidates gets ranked and useless
for testing whether the system recognises a theme returning a week later.
Under a hash, it never can — so a multi-day test built on one would prove
the opposite of what it set out to.

This one places text near other text about the same theme. Each theme owns a
fixed direction, worked out from its name so it is the same on every machine
and in every process. A piece of writing lands along the directions of
whatever themes it mentions, nudged slightly by its own wording so that two
different entries on one theme are close together rather than identical.

**It is told the theme; it does not work it out.** Themes are recognised by
looking for words registered against them. That is not what a real embedding
model does, and this is not pretending to be one. It stands in for the one
property a real model has that a hash does not: writing about the same thing
ends up in the same neighbourhood.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from dataclasses import dataclass, field

from lumen.config import ProviderConfig
from lumen.providers.base import BaseEmbeddingProvider
from lumen.schemas.enums import EmbeddingTaskType

logger = logging.getLogger(__name__)

# How much of a vector comes from its own wording rather than its theme.
#
# Small on purpose. Large enough that two entries on one theme are
# distinguishable and rank against each other sensibly; small enough that
# same-theme always beats different-theme, which is the one property
# anything using this relies on.
WOBBLE = 0.25


@dataclass(frozen=True)
class Theme:
    """
    One subject that entries can be about.

    Attributes:
        name: What the theme is called. Also what fixes its direction, so
            renaming a theme moves it and re-naming it back moves it home.
        keywords: The words that mean an entry touches this theme. Matched
            in lowercase, anywhere in the text.
    """

    name: str
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def appears_in(self, text: str) -> bool:
        """Whether a piece of writing touches this theme."""
        lowered = text.lower()
        return any(keyword.lower() in lowered for keyword in self.keywords)


class ThemedEmbeddingProvider(BaseEmbeddingProvider):
    """
    Turns text into vectors that cluster by what the text is about.

    Same text gives the same vector, always. Two pieces of writing on one
    theme land close together. Two on different themes land far apart. Text
    matching no theme falls back to the plain hash behaviour, so unrelated
    filler does not accidentally cluster with anything.
    """

    provider_name = "themed"

    def __init__(
        self,
        themes: tuple[Theme, ...] = (),
        *,
        model: str = "themed-embedding",
        dimensions: int = 768,
        wobble: float = WOBBLE,
        config: ProviderConfig | None = None,
    ) -> None:
        super().__init__(model, config or ProviderConfig(), dimensions)
        self.themes = themes
        self.wobble = wobble
        self.embedded: list[str] = []
        self.closed = False

    def _embed_chunk(
        self, texts: list[str], task_type: EmbeddingTaskType
    ) -> list[list[float]]:
        self.embedded.extend(texts)
        return [self.vector_for(text) for text in texts]

    def close(self) -> None:
        """Note that it was closed, so a test can check that it happens."""
        self.closed = True

    def themes_in(self, text: str) -> tuple[str, ...]:
        """Which themes a piece of writing touches, in the order registered."""
        return tuple(theme.name for theme in self.themes if theme.appears_in(text))

    def vector_for(self, text: str) -> list[float]:
        """
        Where this piece of writing sits.

        Its themes' directions, averaged, plus a small amount of its own
        wording. Writing that touches nothing registered gets wording alone,
        which is the old hash behaviour and keeps unrelated text from
        drifting together.
        """
        found = [theme for theme in self.themes if theme.appears_in(text)]
        wording = _hashed_direction(text, self.dimensions)

        if not found:
            logger.debug("no registered theme in this text; placing it by wording alone")
            return wording

        subject = _averaged(
            [_hashed_direction(theme.name, self.dimensions) for theme in found]
        )
        return _normalised(
            [
                (1.0 - self.wobble) * axis + self.wobble * noise
                for axis, noise in zip(subject, wording, strict=True)
            ]
        )


def _hashed_direction(seed: str, dimensions: int) -> list[float]:
    """
    A fixed direction for a piece of text, the same everywhere.

    Seeded from a hash rather than from anything about the running process,
    so a test that depends on which of two things is closer gives the same
    answer on every machine and on every run.
    """
    digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=16).digest()
    generator = random.Random(int.from_bytes(digest, "big"))
    return _normalised([generator.gauss(0.0, 1.0) for _ in range(dimensions)])


def _averaged(vectors: list[list[float]]) -> list[float]:
    """The direction midway between several, as a unit vector."""
    if len(vectors) == 1:
        return vectors[0]
    return _normalised([sum(axis) for axis in zip(*vectors, strict=True)])


def _normalised(values: list[float]) -> list[float]:
    """The same direction, scaled to a length of one."""
    length = math.sqrt(sum(value * value for value in values))
    if length == 0:  # pragma: no cover - effectively impossible
        return [0.0] * len(values)
    return [value / length for value in values]


__all__ = ["Theme", "ThemedEmbeddingProvider", "WOBBLE"]
