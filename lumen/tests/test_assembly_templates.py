"""
Turning a record into a sentence.

These are the tests that cannot prove the thing that matters. They can show
that a pattern's briefing names the pattern, carries its count and reads as
English; they cannot show that the sentence is more useful to somebody
mid-conversation than the two it replaced. That judgement is made by reading
real output, which is what the inspection endpoint is for.

What they can pin down is everything that would quietly go wrong: a date
rendered as a timestamp, a quote appearing when somebody is raw, a record of
an unfamiliar kind vanishing instead of falling back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.query.assembly import templates
from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import RetrievalPass

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def node(
    node_type: str = "PatternNode",
    *,
    preview: str = "avoiding going places alone",
    when: datetime | None = None,
    era: str | None = None,
    **properties,
) -> RetrievedNode:
    """One fetched record, with only what a template reads."""
    return RetrievedNode(
        node_id="n_1",
        node_type=node_type,
        preview=preview,
        found_by=RetrievalPass.SEMANTIC,
        similarity=0.7,
        era_tag=era,
        occurred_at=when,
        rank_score=0.7,
        properties=properties,
    )


def rendered(**kwargs) -> str:
    return templates.render(node(**kwargs), now=NOW)


class TestAPattern:
    def test_it_says_what_the_pattern_is(self):
        assert rendered().startswith("Pattern: avoiding going places alone.")

    def test_how_often_it_has_been_seen_is_the_number_that_matters(self):
        # The difference between something they do and something they did
        # once.
        assert "Seen 4 times." in rendered(evidence_count=4)

    def test_a_pattern_seen_once_does_not_boast_about_it(self):
        assert "times" not in rendered(evidence_count=1)

    def test_what_usually_surrounds_it_is_carried(self):
        text = rendered(
            typical_trigger="Being asked to go somewhere new",
            typical_outcome="Cancelling the day before",
        )

        assert "Usually starts with Being asked to go somewhere new." in text
        assert "Usually ends with Cancelling the day before." in text

    def test_where_it_comes_from_is_carried(self):
        assert "Goes back to childhood." in rendered(era="childhood")


class TestABelief:
    def test_it_reads_as_something_they_hold(self):
        text = rendered(node_type="BeliefNode", preview="I fall short of what I meant to be")

        assert text.startswith('Believes: "I fall short of what I meant to be".')

    def test_a_replaced_belief_is_said_in_the_past_tense(self):
        # Knowing they moved past it is useful precisely because they moved
        # past it.
        text = rendered(
            node_type="BeliefNode",
            preview="I fall short",
            status="SUPERSEDED",
        )

        assert text.startswith("Used to believe")
        assert "Since replaced by a later version." in text

    def test_how_long_they_have_held_it_is_carried(self):
        text = rendered(node_type="BeliefNode", when=NOW - timedelta(days=3))

        assert "Held since 3 days ago." in text


class TestTheOtherKinds:
    def test_an_open_question_is_said_as_one(self):
        text = rendered(
            node_type="OpenLoopNode",
            preview="Is it about leaving, or about being alone out there?",
            when=NOW - timedelta(days=1),
        )

        assert text.startswith("Unfinished question from yesterday:")

    def test_a_reframe_is_recognised_by_what_it_is(self):
        text = rendered(
            node_type="ObservationNode",
            preview="the gap is a distance, not a verdict",
            type="CONCEPTUAL_REFRAME",
        )

        assert text.startswith("Reframe they reached")

    def test_a_self_model_is_recognised_too(self):
        text = rendered(
            node_type="ObservationNode",
            preview="I am the kind of person who falls behind",
            type="META_BELIEF",
        )

        assert text.startswith("How they see themselves:")

    def test_something_physical_is_said_as_something_physical(self):
        text = rendered(
            node_type="ObservationNode",
            preview="Chest tightening before the call",
            type="PHYSIOLOGICAL_CAPACITY_STATE",
        )

        assert text.startswith("In the body: Chest tightening")

    def test_an_ordinary_observation_gets_the_plain_form(self):
        text = rendered(
            node_type="ObservationNode", preview="Skipped the run again", type="EMOTION"
        )

        assert text.startswith("Noted:")

    def test_a_lesson_and_a_principle_read_differently(self):
        assert rendered(node_type="LessonNode").startswith("Lesson they drew:")
        assert rendered(node_type="AdoptedPrincipleNode").startswith(
            "Principle they have adopted:"
        )

    def test_a_belief_can_name_the_period_it_formed_in(self):
        text = rendered(node_type="BeliefNode", preview="I fall short", era="hostel")

        assert "Formed around hostel." in text

    def test_a_stretch_of_thinking_is_said_as_one(self):
        assert rendered(
            node_type="SessionNode", preview="working out what the fear was about"
        ).startswith("Worked through:")

    def test_and_says_when_it_was_when_it_knows(self):
        text = rendered(
            node_type="SessionNode",
            preview="working out what the fear was about",
            when=NOW - timedelta(days=1),
        )

        assert text.startswith("Worked through yesterday:")

    def test_an_event_leads_with_when_it_happened(self):
        text = rendered(node_type="EventNode", when=NOW - timedelta(days=1))

        assert text.startswith("Yesterday:")


class TestAnUnfamiliarKind:
    def test_it_still_becomes_a_sentence(self):
        # A missing row in a table should cost a good sentence, never a
        # relevant piece of somebody's history.
        text = rendered(node_type="SomeNewKindOfNode", preview="something recorded")

        assert "something recorded" in text
        assert text.endswith(".")

    def test_every_kind_retrieval_can_return_has_its_own_wording(self):
        # The fallback is for a kind nobody has written yet, not for the ones
        # that already exist.
        from lumen.graph.rows import CONTENT_TABLES

        assert CONTENT_TABLES <= set(templates.TEMPLATES)


class TestQuotingOrNot:
    def test_their_words_are_quoted_by_default(self):
        text = templates.render(
            node(node_type="BeliefNode", preview="I always fall short"),
            now=NOW,
            allow_quotes=True,
        )

        assert '"I always fall short"' in text

    def test_and_are_not_when_they_are_raw(self):
        # Hearing your own sentence repeated back during a bad moment lands
        # as being studied rather than heard.
        text = templates.render(
            node(node_type="BeliefNode", preview="I always fall short"),
            now=NOW,
            allow_quotes=False,
        )

        assert '"' not in text
        assert "I always fall short" in text


class TestSayingWhen:
    @pytest.mark.parametrize(
        "days,expected",
        [
            (0, "earlier today"),
            (1, "yesterday"),
            (3, "3 days ago"),
            (9, "last week"),
            (21, "3 weeks ago"),
        ],
    )
    def test_recent_things_are_said_the_way_people_say_them(self, days, expected):
        assert templates.humanise_date(NOW - timedelta(days=days), NOW) == expected

    def test_older_things_get_a_month(self):
        assert templates.humanise_date(datetime(2026, 3, 2, tzinfo=UTC), NOW) == "in March"

    def test_and_a_year_once_it_is_not_this_one(self):
        assert (
            templates.humanise_date(datetime(2024, 3, 2, tzinfo=UTC), NOW)
            == "in March 2024"
        )

    def test_no_date_says_nothing_rather_than_guessing(self):
        assert templates.humanise_date(None, NOW) == ""

    def test_a_date_in_the_future_is_not_described_as_the_past(self):
        assert templates.humanise_date(NOW + timedelta(days=2), NOW) == "just now"

    def test_a_comparison_works_in_either_direction(self):
        # Whichever side is missing its zone, the two still subtract.
        aware = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
        naive_now = datetime(2026, 8, 17, 12, 0)

        assert templates.humanise_date(aware, naive_now) == "yesterday"

    def test_a_record_written_without_a_time_zone_still_works(self):
        # Records from before time zones were carried consistently. Assuming
        # they share ours is a guess, and the harmless kind.
        naive = datetime(2026, 8, 16, 12, 0)

        assert templates.humanise_date(naive, NOW) == "yesterday"

    def test_a_record_with_no_date_leaves_that_part_out(self):
        assert "Held since" not in rendered(node_type="BeliefNode", when=None)


class TestHowItReads:
    def test_a_briefing_is_one_line(self):
        text = rendered(evidence_count=3, typical_trigger="a\nnew  invitation")

        assert "\n" not in text
        assert "  " not in text

    def test_the_record_s_own_capitalisation_is_left_alone(self):
        # There is no reliable way to tell a name from an ordinary word at
        # the start of a line, and a briefing that mangles the name of
        # somebody's brother is worse than one that reads slightly formally.
        assert "Alex called about it" in rendered(
            node_type="EventNode", preview="Alex called about it"
        )
        assert "I cancelled again" in rendered(
            node_type="EventNode", preview="I cancelled again"
        )
