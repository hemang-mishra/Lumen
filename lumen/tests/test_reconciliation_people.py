"""
Tests for giving the people in someone's writing a record of their own.

This is the half of the stage that makes the search's person anchor work at
all. Until something creates a person record and links findings to it, a
lookup by name has nothing to find — which is exactly the state the previous
stage shipped in, and is fixed here.

The behaviour worth protecting is that a person named across two entries
ends up with one record and not two. Everything else follows from that: the
second entry moves a count rather than creating a duplicate, and every
finding that names them reaches the same record.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lumen.pipeline.reconciliation import people
from lumen.pipeline.reconciliation.contracts import PersonSketch
from lumen.schemas.enums import (
    BookkeepingOperation,
    RelationshipToUser,
    SentimentTrend,
)

AT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


class FailingGraph:
    """A graph that cannot answer anything."""

    def get_node(self, node_id):
        raise RuntimeError("database gone")


class TestSomeoneNewlyNamed:
    def test_they_get_a_record(self, graph_store, make_item):
        item = make_item(person_refs=("Alex",))

        nodes, _, _ = people.resolve_people([item], [], graph=graph_store, at=AT)

        assert [node.node.canonical_name for node in nodes] == ["Alex"]
        assert nodes[0].node.node_id == "person_alex"

    def test_their_record_starts_at_one_mention(self, graph_store, make_item):
        nodes, _, _ = people.resolve_people(
            [make_item(person_refs=("Alex",))], [], graph=graph_store, at=AT
        )

        assert nodes[0].node.mention_count == 1
        assert nodes[0].node.first_mentioned_at == AT

    def test_what_the_entry_said_about_them_is_kept(self, graph_store, make_item):
        nodes, _, _ = people.resolve_people(
            [make_item(person_refs=("Alex",))],
            [PersonSketch(name="Alex", relationship="MANAGER", sentiment="POSITIVE")],
            graph=graph_store,
            at=AT,
        )

        assert nodes[0].node.relationship_to_user is RelationshipToUser.MANAGER
        assert nodes[0].node.relationship_sentiment_trend is SentimentTrend.POSITIVE

    def test_nothing_is_guessed_about_them(self, graph_store, make_item):
        # Guessing a relationship from one mention is how a colleague
        # becomes a friend in somebody's permanent history.
        nodes, _, _ = people.resolve_people(
            [make_item(person_refs=("Alex",))], [], graph=graph_store, at=AT
        )

        assert nodes[0].node.relationship_to_user is RelationshipToUser.UNKNOWN
        assert nodes[0].node.relationship_sentiment_trend is SentimentTrend.UNKNOWN

    def test_a_description_nobody_recognises_is_ignored(self, graph_store, make_item):
        nodes, _, _ = people.resolve_people(
            [make_item(person_refs=("Alex",))],
            [PersonSketch(name="Alex", relationship="NEMESIS", sentiment="SPICY")],
            graph=graph_store,
            at=AT,
        )

        assert nodes[0].node.relationship_to_user is RelationshipToUser.UNKNOWN


class TestSomeoneAlreadyKnown:
    def _seed(self, graph_store, sample_person, name: str = "Alex"):
        """Record someone under the identifier their name produces."""
        known = sample_person.model_copy(
            update={"node_id": people.person_node_id(name), "canonical_name": name}
        )
        graph_store.write_node("PersonEntityNode", known)
        return known

    def test_they_are_not_created_again(self, graph_store, sample_person, make_item):
        known = self._seed(graph_store, sample_person)

        nodes, _, updates = people.resolve_people(
            [make_item(person_refs=(known.canonical_name,))],
            [],
            graph=graph_store,
            at=AT,
        )

        assert nodes == []
        assert [update.operation for update in updates] == [
            BookkeepingOperation.TOUCH_PERSON
        ]

    def test_the_update_points_at_their_existing_record(
        self, graph_store, sample_person, make_item
    ):
        known = self._seed(graph_store, sample_person)

        _, _, updates = people.resolve_people(
            [make_item(person_refs=(known.canonical_name,))],
            [],
            graph=graph_store,
            at=AT,
        )

        assert updates[0].node_id == known.node_id

    def test_a_second_entry_still_links_its_own_findings(
        self, graph_store, sample_person, make_item
    ):
        known = self._seed(graph_store, sample_person)

        _, edges, _ = people.resolve_people(
            [make_item(node_id="obs_later", person_refs=(known.canonical_name,))],
            [],
            graph=graph_store,
            at=AT,
        )

        assert [(edge.from_node_id, edge.to_node_id) for edge in edges] == [
            ("obs_later", known.node_id)
        ]


class TestOnePersonAcrossManyFindings:
    def test_one_record_however_many_findings_name_them(self, graph_store, make_item):
        items = [
            make_item(node_id="obs_1", person_refs=("Alex",)),
            make_item(node_id="obs_2", person_refs=("Alex",)),
        ]

        nodes, edges, _ = people.resolve_people(items, [], graph=graph_store, at=AT)

        assert len(nodes) == 1
        assert len(edges) == 2

    def test_every_finding_that_named_them_is_linked(self, graph_store, make_item):
        items = [
            make_item(node_id="obs_1", person_refs=("Alex",)),
            make_item(node_id="evt_1", node_type="EventNode", person_refs=("Alex",)),
        ]

        _, edges, _ = people.resolve_people(items, [], graph=graph_store, at=AT)

        assert {(edge.table, edge.from_node_id) for edge in edges} == {
            ("mentions_obs", "obs_1"),
            ("mentions_evt", "evt_1"),
        }

    def test_a_name_written_two_ways_still_reaches_one_record(
        self, graph_store, make_item
    ):
        items = [
            make_item(node_id="obs_1", person_refs=("Alex",)),
            make_item(node_id="obs_2", person_refs=("alex",)),
        ]

        nodes, edges, _ = people.resolve_people(items, [], graph=graph_store, at=AT)

        assert len(nodes) == 1
        assert {edge.to_node_id for edge in edges} == {"person_alex"}

    def test_two_different_people_get_two_records(self, graph_store, make_item):
        items = [make_item(person_refs=("Alex", "Sam"))]

        nodes, edges, _ = people.resolve_people(items, [], graph=graph_store, at=AT)

        assert {node.node.canonical_name for node in nodes} == {"Alex", "Sam"}
        assert len(edges) == 2

    def test_an_empty_name_is_skipped(self, graph_store, make_item):
        nodes, _, _ = people.resolve_people(
            [make_item(person_refs=("  ", "Alex"))], [], graph=graph_store, at=AT
        )

        assert [node.node.canonical_name for node in nodes] == ["Alex"]

    def test_nobody_named_means_nothing_to_do(self, graph_store, make_item):
        assert people.resolve_people(
            [make_item()], [], graph=graph_store, at=AT
        ) == ([], [], [])


class TestWhenAFindingCannotLinkToAPerson:
    def test_the_person_is_still_recorded(self, graph_store, make_item):
        # A session records who took part in it differently and has no link
        # of this kind. Losing the person's record over that would be a much
        # bigger loss than losing the one link.
        item = make_item(node_type="SessionNode", node_id="sess_1")
        item = item.model_copy(update={"person_refs": ("Alex",)})

        nodes, edges, _ = people.resolve_people([item], [], graph=graph_store, at=AT)

        assert [node.node.canonical_name for node in nodes] == ["Alex"]
        assert [edge.table for edge in edges] == ["mentions_sess"]


class TestWhenTheGraphCannotAnswer:
    def test_the_person_is_treated_as_new(self, make_item):
        # Planning a record that already exists fails loudly while saving.
        # Skipping one that does not would leave every link to that person
        # pointing at nothing, which fails just as loudly and later.
        nodes, _, updates = people.resolve_people(
            [make_item(person_refs=("Alex",))], [], graph=FailingGraph(), at=AT
        )

        assert len(nodes) == 1
        assert updates == []


class TestTheSearchCanNowFindThem:
    def test_a_saved_person_and_link_are_found_by_name(
        self, graph_store, make_item, sample_observation
    ):
        # The loop the previous stage left open: it built a lookup by person
        # and nothing had ever created a person to look up.
        graph_store.write_node("ObservationNode", sample_observation)
        item = make_item(node_id=sample_observation.node_id, person_refs=("Alex",))

        nodes, edges, _ = people.resolve_people([item], [], graph=graph_store, at=AT)
        for planned in nodes:
            graph_store.write_node(planned.node_type, planned.node)
        for edge in edges:
            graph_store.write_edge(
                edge.table, edge.from_node_id, edge.to_node_id, edge.properties()
            )

        found = graph_store.find_linked_to_person(
            "Alex", node_types=["ObservationNode"]
        )

        assert [row["node_id"] for row in found] == [sample_observation.node_id]


class TestNamingTheSamePersonTwiceInOneFinding:
    def test_the_finding_is_linked_once(self, graph_store, make_item):
        item = make_item(person_refs=("Alex", "Alex"))

        nodes, edges, _ = people.resolve_people([item], [], graph=graph_store, at=AT)

        assert len(nodes) == 1
        assert len(edges) == 1


class TestAKindOfRecordWithNoLinkToAPerson:
    def test_the_person_is_still_recorded_and_the_link_skipped(
        self, graph_store, make_item
    ):
        # Only findings, events and sessions can name a person. Anything
        # else loses its link rather than losing the person.
        item = make_item(person_refs=("Alex",)).model_copy(
            update={"node_type": "PatternNode"}
        )

        nodes, edges, _ = people.resolve_people([item], [], graph=graph_store, at=AT)

        assert [node.node.canonical_name for node in nodes] == ["Alex"]
        assert edges == []
