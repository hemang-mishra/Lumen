"""
Looking at what the system makes of a sentence, and what it goes and finds.

Everything the live conversation layer does is invisible by design — it
happens between somebody speaking and the AI answering, and none of it ever
reaches a screen. That is right for the product and unworkable for anybody
trying to tell whether it works.

So there are two ways in. Paste a sentence and get back exactly what would
have been decided about it; or paste a sentence and get back the records
that decision would have fetched. Between them they answer the only two
questions the logs cannot: whether the reading is any good, and whether the
search finds the right things.

Both are POSTs even though neither changes anything. What is being sent is
somebody's sentence about their own life, and a GET would put that in the
URL, where it lands in every access log and browser history between here and
the server.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from lumen.api.deps import (
    get_composer,
    get_formulator,
    get_memory,
    get_retriever,
    get_sessions,
)
from lumen.api.schemas import (
    BriefingLineView,
    DroppedLineView,
    FormulationRequest,
    FormulationTurn,
    PassReportView,
    PromptView,
    RetrievalRequest,
    RetrievedNodeView,
    TurnContextView,
)
from lumen.query.formulation import QueryFormulator
from lumen.query.memory import ConversationMemory
from lumen.query.memory.contracts import Recollection
from lumen.query.prompting import ChatPrompt, PromptComposer
from lumen.query.retrieval import ConversationalRetriever
from lumen.query.retrieval.contracts import RetrievalBundle, RetrievedNode
from lumen.query.session import ChatSession, SessionRegistry, make_session_id
from lumen.schemas.query import ChatTurn, RetrievalSignal

router = APIRouter(prefix="/query", tags=["query"])

# The conversation these surfaces pretend to be part of when the caller does
# not name one. Every request builds its own, so two callers can never see
# each other's turns and nothing is carried between calls.
DEBUG_USER = "debug"


@router.post("/formulate", response_model=RetrievalSignal)
def formulate_turn(
    body: FormulationRequest,
    formulator: QueryFormulator = Depends(get_formulator),
) -> RetrievalSignal:
    """
    Read one sentence and report what would be looked up because of it.

    Nothing is searched for and nothing is stored. The answer is the
    decision only: which reasons survived being checked against the graph,
    how the person sounds, and how the reading was arrived at — a real model
    call, a shortcut, or a failure.
    """
    session = _throwaway_session()
    _replay(body, session)
    return formulator.formulate(_next_turn(body, session), session)


@router.post("/retrieve", response_model=TurnContextView)
def retrieve_for_turn(
    body: RetrievalRequest,
    formulator: QueryFormulator = Depends(get_formulator),
    retriever: ConversationalRetriever = Depends(get_retriever),
    sessions: SessionRegistry = Depends(get_sessions),
) -> TurnContextView:
    """
    Read one sentence and go and fetch what it points at.

    Both halves come back together, because neither is judgeable alone: the
    records make no sense without the reasons that fetched them, and the
    reasons are hard to assess without seeing what they actually returned.

    Naming a session key continues the same conversation across calls, which
    is the only way to watch the third search work. It exists to notice that
    this turn and an earlier one are about the same thing, and a conversation
    of length one has no earlier one.
    """
    session = _continuing_session(body, sessions)
    _replay(body, session)

    signal = formulator.formulate(_next_turn(body, session), session)
    bundle = retriever.retrieve(signal, session)
    return _as_view(signal, bundle, session)


@router.post("/prompt", response_model=PromptView)
def prompt_for_turn(
    body: RetrievalRequest,
    formulator: QueryFormulator = Depends(get_formulator),
    retriever: ConversationalRetriever = Depends(get_retriever),
    composer: PromptComposer = Depends(get_composer),
    memory: ConversationMemory = Depends(get_memory),
    sessions: SessionRegistry = Depends(get_sessions),
) -> PromptView:
    """
    Show exactly what the assistant would be sent for one sentence.

    The whole of this layer happens between somebody speaking and the
    assistant answering, and none of it ever reaches a screen. That is right
    for the product and leaves nobody able to tell whether the briefing is
    any good — so here it is in full: the instructions, the lines drawn from
    their history, what was fetched and cut, and the conversation as the
    assistant would see it.

    No reply is generated. Writing one is the next goal's job, and a mocked
    one here would be the least useful thing on the page.
    """
    session = _continuing_session(body, sessions)
    _replay(body, session)

    turn = _next_turn(body, session)
    stored = _keep(memory, body, session, turn)

    signal = formulator.formulate(turn, session)
    bundle = retriever.retrieve(signal, session)
    recollection = _recollection(memory, session, stored)
    prompt = composer.compose(
        bundle=bundle, signal=signal, recollection=recollection
    )
    return _as_prompt_view(prompt)


def _keep(
    memory: ConversationMemory,
    body: RetrievalRequest,
    session: ChatSession,
    turn: ChatTurn,
) -> str | None:
    """
    Store this turn, when the caller asked to stay in one conversation, and
    say where it was stored.

    Only then. A one-off request is a question about a sentence, and saving
    every such sentence would fill somebody's history with things nobody
    said to anybody. Naming a conversation is what turns the same call into
    part of one — and it is what gives the memory anything to recall.

    The identifier is handed back rather than assumed. A live session and the
    stored conversation behind it share an identity — the same person, day
    and label — but not a name: the store gives a conversation its own, and
    only that one finds it again.
    """
    if not body.session_key:
        return None

    buffer = memory.store.open(session.user_id, on=session.event_date)
    memory.store.append(
        buffer.session_id, role="user", content=turn.content, at=turn.timestamp
    )
    return buffer.session_id


def _recollection(
    memory: ConversationMemory, session: ChatSession, stored_id: str | None
) -> Recollection:
    """
    What the assistant can still see of this conversation.

    Read from the stored conversation when there is one, since that is the
    only case where there is anything to remember beyond this call. A
    one-off request is answered from the turns it supplied itself, which is
    the honest reading of a conversation that exists for the length of one
    request.
    """
    if stored_id is not None:
        return memory.recall(stored_id)
    return Recollection(
        turns=tuple(session.recent_turns(session.turn_count)),
        total_turns=session.turn_count,
    )


def _as_prompt_view(prompt: ChatPrompt) -> PromptView:
    """Everything the assistant would receive, as the outside may see it."""
    return PromptView(
        system=prompt.system,
        messages=[
            FormulationTurn(role=turn.role, content=turn.content)
            for turn in prompt.messages
        ],
        briefing=[
            BriefingLineView(
                node_id=item.node_id,
                node_type=item.node_type,
                text=item.text,
                tokens=item.tokens,
                found_by=item.found_by.value,
                boosted=item.boosted,
            )
            for item in prompt.context.items
        ],
        dropped=[
            DroppedLineView(node_id=item.node_id, reason=item.reason)
            for item in prompt.context.dropped
        ],
        summary=prompt.summary,
        emotional_register=prompt.context.emotional_register.value,
        token_budget=prompt.context.token_budget,
        briefing_tokens=prompt.context.estimated_tokens,
        total_tokens=prompt.estimated_tokens,
        suppressed=prompt.context.suppressed,
    )


def _replay(body: FormulationRequest, session: ChatSession) -> None:
    """
    Put the supplied history into the session before the new turn arrives.

    Only for a conversation that is not being continued. One that is has
    already recorded its own turns, and adding them again would renumber
    the whole thing.
    """
    if session.turn_count:
        return
    for index, earlier in enumerate(body.history):
        session.record_turn(
            ChatTurn(
                turn_index=index,
                role=earlier.role,
                content=earlier.content,
                timestamp=_now(),
            )
        )


def _next_turn(body: FormulationRequest, session: ChatSession) -> ChatTurn:
    """The sentence being asked about, numbered after whatever came before."""
    return ChatTurn(
        turn_index=session.next_turn_index(),
        role="user",
        content=body.text,
        timestamp=_now(),
    )


def _continuing_session(
    body: RetrievalRequest, sessions: SessionRegistry
) -> ChatSession:
    """The named conversation, or a fresh one nobody will see again."""
    if not body.session_key:
        return _throwaway_session()
    return sessions.open(body.session_key, at=_now())


def _throwaway_session() -> ChatSession:
    """A conversation that exists for one request and is never held onto."""
    today = _now()
    return ChatSession(
        session_id=make_session_id(DEBUG_USER, today.date()),
        user_id=DEBUG_USER,
        event_date=today.date(),
        created_at=today,
        last_activity_at=today,
    )


def _as_view(
    signal: RetrievalSignal, bundle: RetrievalBundle, session: ChatSession
) -> TurnContextView:
    """Turn what happened into what the outside is allowed to see."""
    return TurnContextView(
        signal=signal,
        outcome=bundle.outcome.value,
        candidates=[_as_candidate(node) for node in bundle.candidates],
        passes=[
            PassReportView(
                which=report.which.value,
                ran=report.ran,
                found=report.found,
                kept=report.kept,
                duration_ms=report.duration_ms,
                failure=report.failure,
            )
            for report in bundle.passes
        ],
        latency_ms=bundle.latency_ms,
        within_budget=bundle.within_budget,
        gated=list(bundle.gated),
        buffered=[entry.node_id for entry in session.context_buffer.entries],
    )


def _as_candidate(node: RetrievedNode) -> RetrievedNodeView:
    """One fetched record, without the machinery the next stage needs."""
    return RetrievedNodeView(
        node_id=node.node_id,
        node_type=node.node_type,
        preview=node.preview,
        found_by=node.found_by.value,
        trigger_type=node.trigger_type.value if node.trigger_type else None,
        similarity=node.similarity,
        signal_strength=node.signal_strength.value,
        domain=node.domain.value if node.domain else None,
        era_tag=node.era_tag,
        anchor_type=node.anchor_type.value if node.anchor_type else None,
        anchor_value=node.anchor_value,
        boosted=node.boosted,
        rank_score=node.rank_score,
    )


def _now() -> datetime:
    """The current time, always with a timezone on it."""
    return datetime.now(UTC)


__all__ = ["router"]
