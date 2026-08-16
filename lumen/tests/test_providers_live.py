"""
Tests that talk to real models.

Left out of a normal run, because they need credentials, cost money and depend on
a network. Run them deliberately:

    uv run pytest -m live

They exist because everything else in the suite checks our code against a
stand-in built from our own understanding of the vendor's behaviour. If that
understanding is wrong, only a real call will say so. They deliberately check
shapes rather than wording — a model is free to answer differently every time,
and a test that insisted otherwise would fail for no useful reason.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest
from pydantic import BaseModel

from lumen.config import AppConfig, ProviderConfig
from lumen.providers.gemini import GeminiEmbeddingProvider, GeminiLLMProvider
from lumen.providers.ollama import OllamaEmbeddingProvider, OllamaLLMProvider
from lumen.providers.results import ChatMessage
from lumen.schemas.enums import EmbeddingTaskType, ModelRole

pytestmark = pytest.mark.live


class Sentiment(BaseModel):
    """A small shape, deliberately nested, to check schema conversion for real."""

    label: str
    confidence: float


class Reading(BaseModel):
    """Nested and repeated, which is the shape most likely to trip a converter."""

    summary: str
    findings: list[Sentiment]


def has_gemini_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def ollama_is_running() -> bool:
    host = ProviderConfig().ollama_host
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


needs_gemini = pytest.mark.skipif(not has_gemini_key(), reason="no Gemini credential set")
needs_ollama = pytest.mark.skipif(not ollama_is_running(), reason="no Ollama daemon reachable")


@needs_gemini
class TestGeminiForReal:
    def test_it_returns_json_in_the_shape_asked_for(self):
        provider = GeminiLLMProvider(
            "gemini-2.5-flash", ModelRole.LIGHTWEIGHT, ProviderConfig()
        )
        result = provider.generate_structured(
            "Classify the sentiment of: 'today went better than expected'.",
            Sentiment,
        )
        assert result.data is not None
        assert set(result.data) >= {"label", "confidence"}

    def test_a_nested_shape_also_works(self):
        """
        The real question behind this whole file. Extraction models are deeply
        nested, and a schema converter that cannot take one would only show up
        when that extraction was built.
        """
        provider = GeminiLLMProvider(
            "gemini-2.5-flash", ModelRole.LIGHTWEIGHT, ProviderConfig()
        )
        result = provider.generate_structured(
            "Summarise this and list two sentiment findings: "
            "'I felt anxious this morning but calmer after a walk.'",
            Reading,
        )
        assert result.data is not None
        assert isinstance(result.data.get("findings"), list)

    def test_it_reports_token_usage(self):
        provider = GeminiLLMProvider(
            "gemini-2.5-flash", ModelRole.LIGHTWEIGHT, ProviderConfig()
        )
        result = provider.generate_text([ChatMessage(role="user", content="Say hello.")])
        assert result.usage.total_tokens is not None
        assert result.usage.total_tokens > 0

    def test_embedding_gives_a_vector_of_the_expected_width(self):
        config = ProviderConfig()
        provider = GeminiEmbeddingProvider(config.embedding_model, config)
        vector = provider.embed_text("a quiet evening at home", task_type=EmbeddingTaskType.DOCUMENT)
        assert len(vector) == provider.dimensions

    def test_a_batch_comes_back_complete(self):
        config = ProviderConfig()
        provider = GeminiEmbeddingProvider(config.embedding_model, config)
        vectors = provider.embed_batch(["first thing", "second thing", "third thing"])
        assert len(vectors) == 3

    def test_a_document_and_a_query_embed_differently(self):
        """The reason the task is part of the interface at all."""
        config = ProviderConfig()
        provider = GeminiEmbeddingProvider(config.embedding_model, config)
        text = "seeking reassurance from other people"
        as_document = provider.embed_text(text, task_type=EmbeddingTaskType.DOCUMENT)
        as_query = provider.embed_text(text, task_type=EmbeddingTaskType.QUERY)
        assert as_document != as_query


@needs_ollama
class TestOllamaForReal:
    def test_it_returns_json_in_the_shape_asked_for(self):
        config = ProviderConfig()
        provider = OllamaLLMProvider(config.lightweight_model, ModelRole.LIGHTWEIGHT, config)
        result = provider.generate_structured(
            "Classify the sentiment of: 'today went better than expected'.",
            Sentiment,
        )
        assert result.data is not None

    def test_embedding_gives_a_vector_of_the_expected_width(self):
        config = ProviderConfig(embedding_provider="ollama", embedding_model="nomic-embed-text")
        provider = OllamaEmbeddingProvider(config.embedding_model, config)
        vector = provider.embed_text("a quiet evening at home")
        assert len(vector) == provider.dimensions


@needs_gemini
class TestTheConfiguredSetupWorks:
    def test_everything_configured_can_actually_be_built(self):
        """
        The check that runs at startup, run for real. It is the difference
        between finding a bad setting now and finding it halfway through a batch.
        """
        from lumen.providers.factory import validate_providers

        validate_providers(AppConfig())


@needs_gemini
class TestTheWrittenWeekAgainstRealModels:
    """
    The five-day corpus, read by a real model instead of a stand-in.

    Everything else about the week is checked with scripted replies, which
    proves the machinery between a decision and a changed history — the
    retrieval, the gates, the write plan, the counters. What it cannot prove
    is that a real reading of Wednesday recognises Monday, because a
    stand-in was told the answer.

    That is the question here, and it is asked separately because it is a
    question about prompts and models rather than about code. It needs a
    credential, it costs money, and its answer can change without a single
    line of this repository changing — which is exactly why it must not sit
    in the suite that gates a commit.
    """

    def test_a_real_model_recognises_the_thread_returning(self, tmp_path):
        from lumen.config import OperationalConfig
        from lumen.graph.kuzu_impl import KuzuGraphProvider
        from lumen.operational.engine import create_ops_engine
        from lumen.operational.migrator import upgrade_to_head
        from lumen.operational.sqlalchemy_impl import SQLAlchemyOperationalStore
        from lumen.providers.factory import get_embedding_provider, get_llm_provider
        from lumen.simulation import CORPUS, simulate_days
        from lumen.simulation.corpus import PATTERN_COMPARISON
        from lumen.vector.qdrant_impl import QdrantVectorProvider

        config = AppConfig()
        graph = KuzuGraphProvider(str(tmp_path / "graph"))
        graph.init_schema()
        vectors = QdrantVectorProvider(
            location=":memory:", vector_size=config.vector.vector_size
        )
        vectors.init_collection()
        ops_config = OperationalConfig(db_url=f"sqlite:///{tmp_path / 'ops.db'}")
        engine = create_ops_engine(ops_config)
        upgrade_to_head(engine)
        ops = SQLAlchemyOperationalStore(ops_config, engine=engine)

        try:
            simulate_days(
                CORPUS,
                graph=graph,
                vectors=vectors,
                ops=ops,
                embedder=get_embedding_provider(config),
                models=(
                    get_llm_provider(ModelRole.LIGHTWEIGHT, config),
                    get_llm_provider(ModelRole.THINKING, config),
                ),
                config=config,
            )

            standing = graph.find_nodes(["PatternNode", "BeliefNode"], active_only=False)
            # Deliberately loose. A real model will not reproduce the
            # scripted arc, and demanding that it did would make this a test
            # of prompt phrasing. What is being asked is only whether five
            # days about a handful of themes accumulate at all, or shatter.
            assert standing, "five days produced no standing record of anything"
            assert len(standing) <= 8, (
                "five days about three themes produced "
                f"{len(standing)} separate standing records: "
                + ", ".join(row["node_id"] for row in standing)
            )
        finally:
            graph.close()
            vectors.close()
            ops.close()
