"""
Talking to Lumen over the wire, and reading a day back afterwards.

The conversation is a web socket because a turn is a sequence, not one
answer, and the person is watching part of it. What is tested here is the
sequence arriving intact, the refusals being honest, and the fact that none
of it can reach the graph.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from lumen.tests.conftest import registry_for
from fastapi.testclient import TestClient

from lumen.api.main import create_app
from lumen.config import AppConfig, ChatConfig, ProviderConfig, QueryConfig
from lumen.providers.fake import (
    FakeLLMProvider,
    FakeSpeechProvider,
    FakeTranscriptionProvider,
)
from lumen.query.chat import ChatEngine
from lumen.query.conversation import ConversationStore
from lumen.query.formulation import QueryFormulator
from lumen.query.memory import ConversationMemory
from lumen.query.prompting import PromptComposer
from lumen.query.session import SessionRegistry
from lumen.schemas.enums import ModelRole

REPLY = "That sounds like it took something out of you to do at all."


def a_reading() -> str:
    return json.dumps(
        {
            "triggers": [],
            "emotional_register": "STABLE",
            "named_entities": [],
            "confidence": 0.8,
            "critical_domain_opened": None,
        }
    )


class NoGraph:
    def list_era_tags(self, *, limit=50):
        return []

    def get_node(self, node_id):
        return None

    def find_nodes(self, node_types, **kwargs):
        return []


class NothingFound:
    def retrieve(self, signal, session, *, now=None):
        from lumen.query.retrieval.contracts import RetrievalBundle

        return RetrievalBundle(
            session_id=signal.session_id, turn_index=signal.turn_index
        )


class ReadyStack:
    """A chat stack whose pieces are all scripted, so no model is needed."""

    def __init__(self, ops_store, *, speech=None, listener=None):
        self.memory = ConversationMemory(
            store=ConversationStore(ops_store.buffers),
            llm=FakeLLMProvider(["a summary"] * 30),
            config=ChatConfig(),
        )
        self._engine = ChatEngine(
            formulator=QueryFormulator(
                llm=FakeLLMProvider([a_reading()] * 30),
                stores=registry_for(NoGraph()),
                config=QueryConfig(),
            ),
            retriever=NothingFound(),
            composer=PromptComposer(config=ChatConfig()),
            memory=self.memory,
            sessions=SessionRegistry(QueryConfig()),
            llm=FakeLLMProvider([REPLY] * 30, role=ModelRole.CONVERSATION),
            speech=speech,
            config=ChatConfig(),
        )
        self._listener = listener

    def engine(self):
        return self._engine

    def listener(self):
        if self._listener is None:
            from lumen.providers.errors import ProviderConfigurationError

            raise ProviderConfigurationError("no listener configured")
        return self._listener

    def close(self):
        pass


@pytest.fixture
def chat_client(graph_store, ops_store):
    """A client whose chat stack is scripted rather than built from models."""

    def _build(*, speech=None, listener=None, voice=False):
        from lumen.api.deps import get_graph, get_memory, get_ops

        settings = AppConfig(chat=ChatConfig(voice_enabled=voice))
        app = create_app(settings)
        stack = ReadyStack(ops_store, speech=speech, listener=listener)

        app.dependency_overrides[get_graph] = lambda: graph_store
        app.dependency_overrides[get_ops] = lambda: ops_store
        app.dependency_overrides[get_memory] = lambda: stack.memory
        app.state.graph = graph_store
        app.state.ops = ops_store
        app.state.chat = stack
        app.state.config = settings
        return TestClient(app, raise_server_exceptions=False)

    return _build


def talk(
    client, text: str, *, expect_audio: bool = False, **extra
) -> list[dict]:
    """
    Say one thing and collect every event that comes back.

    Stops on the reply, or on the audio when audio was expected. Reading past
    the last event a turn produces would wait forever, which is exactly what
    happens when a test asks to be spoken to and the deployment has no voice.
    """
    with client.websocket_connect("/chat/ws") as socket:
        socket.send_json({"text": text, **extra})
        events = []
        while True:
            event = socket.receive_json()
            events.append(event)
            if event["kind"] == "error":
                break
            if event["kind"] == "audio.reply":
                break
            if event["kind"] == "reply.done" and not expect_audio:
                break
        return events


class TestHoldingAConversation:
    def test_the_whole_turn_arrives_in_order(self, chat_client):
        events = talk(chat_client(), "I went for a walk on my own today")

        kinds = [event["kind"] for event in events]
        assert kinds[0] == "turn.accepted"
        assert kinds[1] == "context.ready"
        assert "reply.delta" in kinds
        assert kinds[-1] == "reply.done"

    def test_the_reply_arrives_in_pieces_that_join_up(self, chat_client):
        events = talk(chat_client(), "I went for a walk")

        streamed = "".join(
            event["text"] for event in events if event["kind"] == "reply.delta"
        )
        done = next(e for e in events if e["kind"] == "reply.done")
        assert streamed.strip() == done["text"] == REPLY

    def test_what_was_gathered_is_reported_for_anybody_looking(self, chat_client):
        events = talk(chat_client(), "I went for a walk")

        gathered = next(e for e in events if e["kind"] == "context.ready")
        assert gathered["emotional_register"] == "STABLE"
        assert "retrieval_ms" in gathered

    def test_saying_nothing_is_refused_without_closing_the_socket(self, chat_client):
        client = chat_client()
        with client.websocket_connect("/chat/ws") as socket:
            socket.send_json({"text": "   "})
            refusal = socket.receive_json()

            assert refusal["kind"] == "error"
            assert refusal["reason"] == "empty_turn"

            # Still usable: a bad turn should not end the conversation.
            socket.send_json({"text": "I went for a walk"})
            assert socket.receive_json()["kind"] == "turn.accepted"

    def test_several_turns_run_on_one_connection(self, chat_client):
        client = chat_client()
        with client.websocket_connect("/chat/ws") as socket:
            indexes = []
            for said in ("first thing", "second thing"):
                socket.send_json({"text": said})
                while True:
                    event = socket.receive_json()
                    if event["kind"] == "turn.accepted":
                        indexes.append(event["turn_index"])
                    if event["kind"] == "reply.done":
                        break

            assert indexes == [0, 1]


class TestSpeaking:
    def test_the_reply_comes_back_as_audio_when_asked(self, chat_client):
        client = chat_client(speech=FakeSpeechProvider(), voice=True)

        events = talk(client, "I went for a walk", speak=True, expect_audio=True)

        spoken = next(e for e in events if e["kind"] == "audio.reply")
        assert spoken["mime_type"] == "audio/wav"
        assert spoken["audio"]

    def test_the_audio_is_sent_as_text_so_it_survives_the_socket(self, chat_client):
        import base64

        client = chat_client(speech=FakeSpeechProvider(), voice=True)

        events = talk(client, "I went for a walk", speak=True, expect_audio=True)

        spoken = next(e for e in events if e["kind"] == "audio.reply")
        assert base64.b64decode(spoken["audio"])

    def test_a_deployment_with_voice_switched_off_still_talks(self, chat_client):
        client = chat_client(speech=FakeSpeechProvider(), voice=False)

        events = talk(client, "I went for a walk", speak=True)

        assert [e["kind"] for e in events][-1] == "reply.done"


class TestListening:
    def test_a_recording_comes_back_as_words(self, chat_client):
        client = chat_client(listener=FakeTranscriptionProvider(["I went for a walk"]))

        answer = client.post(
            "/chat/transcribe",
            files={"audio": ("said.webm", b"pretend audio", "audio/webm")},
        )

        assert answer.status_code == 200
        assert answer.json()["text"] == "I went for a walk"

    def test_an_empty_recording_is_refused(self, chat_client):
        client = chat_client(listener=FakeTranscriptionProvider(["anything"]))

        answer = client.post(
            "/chat/transcribe", files={"audio": ("said.webm", b"", "audio/webm")}
        )

        assert answer.status_code == 503

    def test_a_deployment_that_cannot_listen_says_so(self, chat_client):
        answer = chat_client().post(
            "/chat/transcribe",
            files={"audio": ("said.webm", b"pretend audio", "audio/webm")},
        )

        assert answer.status_code == 503


class TestReadingADayBack:
    def test_the_days_that_hold_a_conversation_are_listed(self, chat_client):
        client = chat_client()
        talk(client, "I went for a walk")

        days = client.get("/chat/days").json()

        assert days
        assert days[0]["message_count"] == 2

    def test_a_day_says_whether_it_can_still_be_changed(self, chat_client):
        client = chat_client()
        talk(client, "I went for a walk")

        assert client.get("/chat/days").json()[0]["editable"] is True

    def test_one_day_can_be_read_as_the_person_would_read_it(self, chat_client):
        client = chat_client()
        talk(client, "I went for a walk")
        today = date.today().isoformat()

        thread = client.get(f"/chat/days/{today}").json()

        assert [message["role"] for message in thread["messages"]] == [
            "user",
            "assistant",
        ]


class TestEditing:
    def test_a_turn_can_be_said_differently_while_the_day_is_open(self, chat_client):
        client = chat_client()
        events = talk(client, "what I said")
        accepted = events[0]

        answer = client.post(
            f"/chat/messages/{accepted['message_id']}/revise",
            json={"session_id": accepted["session_id"], "content": "what I meant"},
        )

        assert answer.status_code == 200
        assert answer.json()["content"] == "what I meant"

    def test_a_processed_day_refuses_and_says_what_to_do_instead(
        self, chat_client, ops_store
    ):
        from lumen.operational.enums import BufferStatus

        client = chat_client()
        events = talk(client, "what I said")
        accepted = events[0]
        ops_store.buffers.mark_status(
            accepted["session_id"], BufferStatus.PROCESSED
        )

        answer = client.post(
            f"/chat/messages/{accepted['message_id']}/revise",
            json={"session_id": accepted["session_id"], "content": "what I meant"},
        )

        assert answer.status_code == 409
        assert "say it again today" in answer.json()["instead"]


class TestRefusingProperly:
    def test_a_recording_larger_than_the_limit_is_refused(self, chat_client):
        """
        Checked before anything is sent anywhere. A very large upload should
        be turned away at the door rather than paid for.
        """
        from lumen.api.deps import get_config

        client = chat_client(listener=FakeTranscriptionProvider(["anything"]))
        client.app.dependency_overrides[get_config] = lambda: AppConfig(
            chat=ChatConfig(max_audio_bytes=4)
        )

        answer = client.post(
            "/chat/transcribe",
            files={"audio": ("said.webm", b"far too much audio", "audio/webm")},
        )

        assert answer.status_code == 503
        assert "larger than" in answer.json()["detail"]

    def test_a_deployment_that_cannot_talk_at_all_says_so(self, graph_store, ops_store):
        from lumen.api.deps import get_graph, get_ops

        app = create_app(AppConfig())
        app.dependency_overrides[get_graph] = lambda: graph_store
        app.dependency_overrides[get_ops] = lambda: ops_store
        app.state.graph = graph_store
        app.state.ops = ops_store
        app.state.chat = None
        client = TestClient(app, raise_server_exceptions=False)

        answer = client.post(
            "/chat/transcribe",
            files={"audio": ("said.webm", b"pretend audio", "audio/webm")},
        )

        assert answer.status_code == 503

    def test_a_turn_with_no_model_behind_it_is_answered_not_dropped(
        self, chat_client
    ):
        from lumen.providers.errors import ProviderConfigurationError

        client = chat_client()

        def refuse():
            raise ProviderConfigurationError("no conversation model")

        client.app.state.chat.engine = refuse

        with client.websocket_connect("/chat/ws") as socket:
            socket.send_json({"text": "I went for a walk"})
            refusal = socket.receive_json()

        assert refusal["kind"] == "error"
        assert refusal["reason"] == "no_model"

    def test_a_spoken_turn_is_marked_as_spoken(self, chat_client, ops_store):
        client = chat_client()

        events = talk(client, "I went for a walk", spoken=True)

        stored = ops_store.buffers.get_messages(events[0]["session_id"])
        assert stored[0].modality == "VOICE"
