"""
Wiring a conversation together, and running one from a terminal.

Two things live here and they are deliberately separate. Building the engine
is fiddly — six collaborators, several of which need models — and it is
exactly the same wiring the web service does, so it is written once and used
by both the terminal and the tests. Running the conversation is the printing.

Nothing here decides anything about a turn. It opens stores, hands them over,
and shows what came back.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from lumen.api.resources import LazySearchStack
from lumen.config import AppConfig
from lumen.stores import StoreRegistry
from lumen.operational.migrator import upgrade_to_head
from lumen.operational.sqlalchemy_impl import build_operational_store
from lumen.providers.errors import ProviderError
from lumen.providers.factory import get_llm_provider, get_speech_provider
from lumen.query.chat import (
    ChatEngine,
    ContextReady,
    ReplyDelta,
    ReplyDone,
    TurnEvent,
    TurnFailed,
)
from lumen.query.conversation import ConversationStore
from lumen.query.formulation import QueryFormulator
from lumen.query.alerts import ShadowAlertReader
from lumen.query.frequency import QueryHitRecorder
from lumen.query.memory import ConversationMemory
from lumen.query.prompting import PersonaStore, PromptComposer
from lumen.query.session import SessionRegistry
from lumen.schemas.enums import ModelRole

# Who is talking. One person, until there is a reason for more.
USER = "debug"


@dataclass
class ChatRunner:
    """
    A conversation and everything it needs, with one way to close it all.

    Holds the pieces rather than hiding them so a caller can reach past the
    printing — a test wants the events, not the words on a screen.
    """

    engine: ChatEngine
    _closers: tuple

    def say(self, text: str, **kwargs) -> Iterator[TurnEvent]:
        """One thing the person said, and everything that happens because of it."""
        return self.engine.say(USER, text, **kwargs)

    def close(self) -> None:
        """Release everything that was opened, in the order it was opened."""
        for close in reversed(self._closers):
            try:
                close()
            except Exception:
                pass


@contextmanager
def build_runner(config: AppConfig | None = None) -> Iterator[ChatRunner]:
    """
    Open every store and model a conversation needs, and close them after.

    The same wiring the web service does, in one place, so the terminal and
    the service cannot drift into having different conversations with the
    same person.
    """
    settings = config or AppConfig()

    # The command line has no request to carry an identity, so it uses the
    # configured default — the one person this machine belongs to. Everything
    # below asks the registry for their stores exactly as the service does.
    stores = StoreRegistry(settings)
    store = build_operational_store(settings)
    upgrade_to_head(store.engine)

    search = LazySearchStack(config=settings, stores=stores, worker=None)
    sessions = SessionRegistry(settings.query)
    memory = ConversationMemory(
        store=ConversationStore(store.buffers),
        llm=_quiet(lambda: get_llm_provider(ModelRole.LIGHTWEIGHT, settings)),
        config=settings.chat,
    )

    # One retry for the turn reader, where every other call in the system
    # gets three. It used to get none: under a sub-second deadline a second
    # attempt could not have finished. The deadline is seconds now, and a
    # dropped connection at half a second leaves room for one more go — so
    # the turn keeps its retrieval instead of losing it to a blip.
    one_retry = replace(settings.providers, max_attempts=2)
    formulator = QueryFormulator(
        llm=get_llm_provider(
            ModelRole.LIGHTWEIGHT, replace(settings, providers=one_retry)
        ),
        stores=stores,
        config=settings.query,
    )

    engine = ChatEngine(
        formulator=formulator,
        retriever=search.get(),
        composer=PromptComposer(config=settings.chat),
        memory=memory,
        sessions=sessions,
        personas=PersonaStore(settings=store.settings),
        llm=get_llm_provider(ModelRole.CONVERSATION, settings),
        speech=_quiet(lambda: get_speech_provider(settings))
        if settings.chat.voice_enabled
        else None,
        hits=QueryHitRecorder(stores, config=settings.scoring),
        alerts=ShadowAlertReader(stores, config=settings.macro),
        config=settings.chat,
    )

    runner = ChatRunner(
        engine=engine,
        _closers=(stores.close, store.close, search.close, formulator.close),
    )
    try:
        yield runner
    finally:
        runner.close()


def converse(
    runner: ChatRunner,
    lines: Iterable[str],
    *,
    show: bool = True,
    echo: bool = True,
) -> None:
    """
    Run a conversation, printing the reply and what was behind it.

    The reply is printed as it arrives, because that is the thing being
    judged. What was gathered is printed underneath rather than before, so
    the words are read first and the machinery second.

    `echo` is off when somebody is typing, since they have just watched
    themselves type it.
    """
    for line in lines:
        said = line.strip()
        if not said:
            continue
        if show and echo:
            _write(f"\nyou: {said}\n")
        if show:
            _write("\nlumen: ")

        gathered: ContextReady | None = None
        for event in runner.say(said):
            if isinstance(event, ReplyDelta) and show:
                _write(event.text)
            elif isinstance(event, ContextReady):
                gathered = event
            elif isinstance(event, ReplyDone) and show:
                _write("\n")
                _write(_behind_it(gathered, event))
            elif isinstance(event, TurnFailed) and show:
                _write(f"\n[{event.reason}] {event.detail}\n")


def _behind_it(gathered: ContextReady | None, done: ReplyDone) -> str:
    """What was decided and fetched, in a few dim lines under the reply."""
    if gathered is None:
        return ""

    lines = [
        f"\n  ── {gathered.emotional_register.lower()}"
        f" · {len(gathered.briefing)} from your history"
        f" · {gathered.previous_days} earlier days"
        f" · first word in {done.first_chunk_ms}ms"
    ]
    lines.extend(f"     · {line}" for line in gathered.briefing)
    if gathered.withheld:
        lines.append(f"     · {len(gathered.withheld)} held back until you raise it")
    if gathered.search_failed:
        lines.append("     · your history could not be reached this turn")
    return "\n".join(lines) + "\n"


def _quiet(build):
    """
    Build something optional, and shrug if it is not configured.

    Used for the two models a conversation can do without. A missing
    summariser costs a long chat some coherence; a missing voice costs the
    spoken half. Neither is a reason to refuse to talk at all.
    """
    try:
        return build()
    except ProviderError:
        return None


def _write(text: str) -> None:
    """Print without a newline and without waiting for one."""
    sys.stdout.write(text)
    sys.stdout.flush()


__all__ = ["ChatRunner", "build_runner", "converse", "USER"]
