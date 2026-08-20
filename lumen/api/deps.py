"""
What a request is given to work with.

Most of these are narrower than they could be. The graph arrives as a reader
— every question, none of the writes — so a route that tried to change
something would be reaching for a method its own type does not have. The
operational store arrives whole, because reading a run's history is all
anything here does with it and there is no read-only half to hand over. The
turn reader arrives already built, because it holds a model connection that
is not worth opening per request.

The importer is the exception, and the one thing here that can write. It is
still narrow, but in a different way: it is not a graph handle at all. What
a route can do with it is put an identifier on a queue. The graph, the
vector store and the models live inside it, on its own thread, and no route
ever sees them.

All of them are resolved per request from what the application opened at
startup, rather than reached for directly. That is what lets a test point
the whole API at temporary databases by replacing a couple of functions.
"""

from __future__ import annotations

from fastapi import Request, WebSocket
from starlette.requests import HTTPConnection

from lumen.api.errors import Unavailable
from lumen.api.events import EventBus
from lumen.auth import AuthService, Identity
from lumen.erasure import ErasureService
from lumen.config import AppConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.ingest import IngestWorker
from lumen.operational.repositories import OperationalStore
from lumen.pipeline.macroextraction.service import MacroextractionService
from lumen.review.service import ReviewService
from lumen.providers.errors import ProviderError
from lumen.query import (
    ConversationalRetriever,
    ConversationMemory,
    PromptComposer,
    QueryFormulator,
    SessionRegistry,
)
from lumen.query.prompting import PersonaStore


def get_graph(request: Request) -> ReadOnlyGraph:
    """
    The graph, to read from.

    Typed as a reader on purpose. Adding a write endpoint would not merely
    be poor judgement — the method is not on what this hands back, so it
    would fail before it ran.
    """
    return request.app.state.graph


def get_ops(request: Request) -> OperationalStore:
    """The record of what past runs did."""
    return request.app.state.ops


def get_config(request: Request) -> AppConfig:
    """The settings this application was built with."""
    return request.app.state.config


def get_worker(request: Request) -> IngestWorker:
    """
    The thing that runs imports.

    The one object in the application that can change the graph, and it is
    handed out only to the routes that accept uploads. What a route can do
    with it is put an identifier on a queue — the graph, the vector store
    and the models all live inside it and are never exposed.

    Absent when this deployment was told not to accept uploads, in which
    case the routes that would use it were never mounted, so nothing can
    reach this. It is still checked, because "the router was not included"
    is a fact about startup and this is a fact about a request.
    """
    worker = getattr(request.app.state, "ingest", None)
    if worker is None:
        raise Unavailable("importing", "this deployment does not accept uploads")
    return worker


def get_reporter(request: Request) -> MacroextractionService:
    """
    The thing that builds periodic reports.

    The second object in this file that can change the graph, and narrow in
    the same way as the first: it is not a graph handle. What a route can do
    with it is name a period and ask for it to be summarised — the store and
    the models live inside it and are never handed out.

    Built once at startup, because it holds the writable graph and caches the
    models it eventually needs. Absent only when the application was built
    without one, which nothing in this deployment does.
    """
    reporter = getattr(request.app.state, "reporter", None)
    if reporter is None:
        raise Unavailable("building reports", "this deployment cannot write reports")
    return reporter


def get_auth(connection: HTTPConnection) -> AuthService:
    """
    The thing that decides who is asking.

    Present whether or not sign-in is switched on, because the routes that
    use it are mounted either way and answering "this deployment has no
    sign-in" is better than a route that does not exist.
    """
    service = getattr(connection.app.state, "auth", None)
    if service is None:
        raise Unavailable("sign-in", "this deployment has no sign-in configured")
    return service


def get_identity(connection: HTTPConnection) -> Identity:
    """
    Who is asking.

    Every route that touches a person's data depends on this rather than on
    configuration. A route that read configuration would serve whoever the
    process was started as, which in a deployment with two people is a data
    leak that looks exactly like working software.

    With sign-in switched off this answers the configured default, and that
    single seam is what lets the existing single-user deployment and the
    whole test suite carry on unchanged.

    Raises:
        NotAuthenticated: There is no usable proof of who this is. Carries a
            short reason a person can act on, and never says whether the
            account it names exists.
    """
    settings: AppConfig = connection.app.state.config
    if not settings.auth.enabled:
        return Identity(
            user_id=settings.default_user_id,
            email="",
            authenticated=False,
        )

    cached = getattr(connection.state, "identity", None)
    if cached is not None:
        return cached

    identity = get_auth(connection).identify(_bearer(connection))
    connection.state.identity = identity
    return identity


def require_identity(connection: HTTPConnection) -> Identity:
    """
    The same, mounted as a router-level default.

    The difference is where it is used rather than what it does. Routers take
    this as a default dependency, so an endpoint added without a thought
    about who may call it is protected — the failure mode of forgetting is a
    refusal rather than a leak.
    """
    return get_identity(connection)


def _bearer(connection: HTTPConnection) -> str:
    """
    The token a caller is carrying, if it is carrying one.

    From the header for an ordinary request, and only from the header: a
    token in a query string ends up in access logs, browser history and
    referrer headers, which is three permanent copies of a credential.

    A socket is the exception, and it is a real cost rather than a
    convenience. A browser's WebSocket API cannot set a header, so a
    conversation could not be authenticated at all otherwise. What travels
    that way is the short-lived half of a session, never the renewable one.
    """
    header = connection.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()

    if isinstance(connection, WebSocket):
        return (connection.query_params.get("access_token") or "").strip()
    return ""


def get_events(request: Request) -> EventBus:
    """
    Where things that happen are announced.

    Always present, because publishing to nobody is the ordinary state and
    costs nothing. A deployment with the clock switched off still has one; it
    simply has less to say.
    """
    return request.app.state.events


def get_eraser(request: Request) -> ErasureService:
    """
    The thing that forgets.

    Narrow in the same way as the other objects here that can change the
    graph, and more so, because what it does cannot be undone. What a route
    can do with one of these is ask what an erasure would cover and ask for
    one to happen — with the confirmation phrase, which is checked inside.
    """
    eraser = getattr(request.app.state, "eraser", None)
    if eraser is None:
        raise Unavailable("erasure", "this deployment cannot erase data")
    return eraser.get()


def get_reviewer(request: Request) -> ReviewService:
    """
    The thing that answers review questions.

    Narrow in the same way as the other two objects here that can change the
    graph. What a route can do with one of these is list what is waiting,
    answer one, defer one, or run the housekeeping. There is no way to reach
    the graph through it, so no route can grow a way to write something
    nobody was asked about.
    """
    reviewer = getattr(request.app.state, "reviewer", None)
    if reviewer is None:
        raise Unavailable("the review queue", "this deployment cannot answer reviews")
    return reviewer


def get_formulator(request: Request) -> QueryFormulator:
    """
    The thing that reads a conversational turn.

    Opened once when the application starts, because it holds a model
    connection and a small pool of threads. Building one per request would
    pay for both on every call, which is the opposite of what a component
    with a sub-second budget wants.

    It is the one part of this service that needs a model, so it is also the
    only part that can be missing. Saying so plainly beats a generic failure:
    the fix is to configure a model, and nothing else here is affected.
    """
    formulator = getattr(request.app.state, "formulator", None)
    if formulator is None:
        raise Unavailable(
            "reading conversational turns", "no language model is configured"
        )
    return formulator


def get_retriever(request: Request) -> ConversationalRetriever:
    """
    The thing that fetches a turn's history.

    Built on the first request that needs it rather than at startup, because
    it needs a search index and a model while every other route needs
    neither. A deployment with nothing configured still starts, still serves
    the graph, and refuses only this.

    The graph inside it is a reader, so this whole surface remains incapable
    of changing anything.
    """
    stack = getattr(request.app.state, "search", None)
    if stack is None:
        raise Unavailable("fetching history", "this deployment cannot search")
    try:
        return stack.get()
    except ProviderError as exc:
        raise Unavailable(
            "fetching history", "no language model or embedder is configured"
        ) from exc


def get_composer(request: Request) -> PromptComposer:
    """
    The thing that builds what the assistant is sent.

    Held on the application rather than made per request, though it would
    cost almost nothing to build one: it carries the settings that decide how
    much history a turn may hold, and having one copy of those means changing
    them changes every turn rather than the next one.
    """
    return request.app.state.composer


def get_personas(request: Request) -> PersonaStore:
    """
    The thing that knows how each person has asked to be spoken to.

    Reads and writes one row of the settings table and can reach nothing
    else. Worth stating plainly, because this is the only handle in this file
    that lets a caller change what a model is told: what it can change is
    three paragraphs of tone, and the parts about distress are not fields on
    what it hands back, so there is no request that could reach them.
    """
    return request.app.state.personas


def get_memory(request: Request) -> ConversationMemory:
    """
    The thing that remembers the conversation itself.

    Distinct from the graph, which remembers the person. This one only knows
    about today, and it reads and writes the same store the extraction
    pipeline eventually consumes — which is what makes a chat become history
    rather than being a separate thing that resembles one.
    """
    return request.app.state.memory


def get_chat_stack(request: Request):
    """
    The thing that holds a conversation, and the ear that listens to one.

    Built on the first turn rather than at startup, because talking needs a
    model and every other route here reads two local databases and needs
    none. A deployment with nothing configured still starts, still serves the
    graph, and refuses only this.
    """
    stack = getattr(request.app.state, "chat", None)
    if stack is None:
        raise Unavailable("talking", "this deployment cannot hold a conversation")
    return stack


def get_sessions(request: Request) -> SessionRegistry:
    """
    The live conversations this process is holding.

    Kept on the application rather than made per request, because a
    conversation that forgets itself between messages has no thread to
    follow — and following the thread is what one of the three searches
    does.
    """
    return request.app.state.sessions


__all__ = [
    "get_graph",
    "get_ops",
    "get_config",
    "get_worker",
    "get_formulator",
    "get_retriever",
    "get_composer",
    "get_memory",
    "get_sessions",
    "get_chat_stack",
]
