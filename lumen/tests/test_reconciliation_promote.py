"""
Tests for deciding what becomes a lasting record.

The rule being protected here is the difference between a graph of a few
hundred meaningful records and one with ten thousand one-off notes. Most of
what a day produces — where someone was, what they did, how tired they felt
— belongs to that day. A much smaller set are claims about how the person
works, and only those earn a record that will still be retrieved years from
now.

The table saying which is which is checked for completeness rather than for
its contents, because the contents will change and the completeness must
not: a finding type added later with no entry should fail the suite, not
quietly default to being thrown away.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.pipeline.reconciliation import promote
from lumen.pipeline.reconciliation.catalog import PROMOTION, PromotionTarget
from lumen.pipeline.reconciliation.contracts import NewNodeContent
from lumen.schemas.enums import (
    Domain,
    ObservationType,
    Provenance,
    SignalStrength,
    VerificationStatus,
)

AT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


def never_taken(_node_id: str) -> bool:
    return False


def content(
    kind: str = "PATTERN",
    *,
    name: str = "Comparison spiral",
    statement: str = "Comparing himself to peers and feeling behind",
    domain: str = "EMOTIONAL",
) -> NewNodeContent:
    return NewNodeContent(kind=kind, name=name, statement=statement, domain=domain)


class TestWhichFindingsCanLast:
    def test_every_kind_of_finding_has_been_decided_about(self):
        # The point of asserting this is the types nobody has thought about
        # yet. A new one with no entry should stop the suite and make
        # somebody choose, rather than silently belonging to its day.
        assert set(PROMOTION) == set(ObservationType)

    @pytest.mark.parametrize(
        "observation_type",
        [
            ObservationType.BELIEF,
            ObservationType.CORE_WOUND,
            ObservationType.META_BELIEF,
            ObservationType.PERSPECTIVE_SHIFT,
        ],
    )
    def test_claims_about_the_self_can_become_beliefs(self, observation_type):
        assert PROMOTION[observation_type] is PromotionTarget.BELIEF

    @pytest.mark.parametrize(
        "observation_type",
        [
            ObservationType.PATTERN,
            ObservationType.RUMINATION_LOOP,
            ObservationType.COGNITIVE_DISTORTION,
            ObservationType.RELATIONAL_DYNAMIC,
        ],
    )
    def test_repeating_behaviour_can_become_patterns(self, observation_type):
        assert PROMOTION[observation_type] is PromotionTarget.PATTERN

    @pytest.mark.parametrize(
        "observation_type",
        [
            ObservationType.CONTEXT,
            ObservationType.EMOTION,
            ObservationType.SOMATIC_STATE,
            ObservationType.ENVIRONMENTAL_CONTEXT,
            ObservationType.GRATITUDE_APPRECIATION,
        ],
    )
    def test_the_texture_of_a_day_stays_with_the_day(self, observation_type):
        assert PROMOTION[observation_type] is None

    def test_a_finding_with_no_kind_cannot_last(self, make_item):
        # Events and sessions have no category of finding, and neither ever
        # becomes a belief or a pattern.
        assert promote.can_promote(make_item(node_type="EventNode")) is False


class TestBuildingALastingRecord:
    def test_a_belief_takes_its_wording_from_the_model(self, make_item):
        item = make_item(observation_type=ObservationType.BELIEF)

        planned = promote.build_standing_node(
            item,
            content("BELIEF", name="Needs solitude", statement="I recharge alone"),
            at=AT,
            exists=never_taken,
        )

        assert planned.node_type == "BeliefNode"
        assert planned.node.belief_statement == "I recharge alone"
        assert planned.node.node_id == "bel_needs_solitude"

    def test_a_belief_takes_its_weight_and_origin_from_the_finding(self, make_item):
        # The model writes the words; everything about how much the finding
        # counts comes from the finding, which is the part a model should
        # not get a second go at.
        item = make_item(
            observation_type=ObservationType.BELIEF,
            signal=SignalStrength.CRITICAL,
            provenance=Provenance.CO_CREATED,
        )

        planned = promote.build_standing_node(
            item, content("BELIEF"), at=AT, exists=never_taken
        )

        assert planned.node.signal_strength is SignalStrength.CRITICAL
        assert planned.node.provenance is Provenance.CO_CREATED
        assert planned.node.verification_status is VerificationStatus.UNVERIFIED

    def test_a_pattern_keeps_both_its_name_and_its_description(self, make_item):
        planned = promote.build_standing_node(
            make_item(observation_type=ObservationType.PATTERN),
            content("PATTERN", name="Comparison spiral", statement="Compares and sinks"),
            at=AT,
            exists=never_taken,
        )

        assert planned.node.pattern_name == "Comparison spiral"
        assert planned.node.pattern_description == "Compares and sinks"
        assert planned.node.domain is Domain.EMOTIONAL

    def test_a_new_record_starts_with_one_piece_of_evidence(self, make_item):
        planned = promote.build_standing_node(
            make_item(), content(), at=AT, exists=never_taken
        )

        assert planned.node.evidence_count == 1

    def test_a_new_record_is_marked_for_searching(self, make_item):
        # Without this it would exist in the graph and be findable by
        # nothing, which is the same as not existing.
        planned = promote.build_standing_node(
            make_item(), content(statement="Compares and sinks"), at=AT, exists=never_taken
        )

        assert planned.searchable_text == "Compares and sinks"

    def test_the_texture_of_a_day_produces_nothing(self, make_item):
        planned = promote.build_standing_node(
            make_item(observation_type=ObservationType.EMOTION),
            content(),
            at=AT,
            exists=never_taken,
        )

        assert planned is None

    def test_missing_wording_falls_back_to_the_finding(self, make_item):
        # A slightly clumsy record is worth far more than a decision thrown
        # away for want of a sentence.
        item = make_item("I keep checking how everyone else is doing")

        planned = promote.build_standing_node(item, None, at=AT, exists=never_taken)

        assert planned.node.pattern_description == item.text

    def test_an_unrecognised_area_of_life_does_not_lose_the_record(self, make_item):
        planned = promote.build_standing_node(
            make_item(), content(domain="VIBES"), at=AT, exists=never_taken
        )

        assert planned.node.domain is Domain.SELF_CONCEPT


class TestIdentifiersStayReadableAndUnique:
    def test_a_name_becomes_a_readable_identifier(self, make_item):
        planned = promote.build_standing_node(
            make_item(), content(name="Decision saturation"), at=AT, exists=never_taken
        )

        assert planned.node.node_id == "pat_decision_saturation"

    def test_a_name_already_taken_gets_the_date_added(self, make_item):
        # Two records can genuinely deserve the same name. An identifier
        # already in use would stop the whole entry from saving.
        planned = promote.build_standing_node(
            make_item(),
            content(name="Decision saturation"),
            at=AT,
            exists=lambda node_id: node_id == "pat_decision_saturation",
        )

        assert planned.node.node_id == "pat_decision_saturation_2026_06_11"

    def test_a_name_taken_twice_over_keeps_counting(self, make_item):
        taken = {"pat_decision_saturation", "pat_decision_saturation_2026_06_11"}

        planned = promote.build_standing_node(
            make_item(),
            content(name="Decision saturation"),
            at=AT,
            exists=lambda node_id: node_id in taken,
        )

        assert planned.node.node_id == "pat_decision_saturation_2026_06_11_2"


class TestTheNextVersionOfARecord:
    def test_it_carries_the_old_one_forward(self):
        existing = {
            "_label": "BeliefNode",
            "node_id": "bel_solitude",
            "version": 1,
            "evidence_count": 4,
            "domain": "SELF_CONCEPT",
            "signal_strength": "HIGH",
            "provenance": "USER_GENERATED",
            "belief_source_summary": "said so in March",
        }

        planned = promote.next_version(
            existing,
            statement="I recharge with people I trust",
            delta="solitude stopped being the only way",
            at=AT,
            took_ownership=False,
        )

        assert planned.node.version == 2
        assert planned.node.previous_version_id == "bel_solitude"
        assert planned.node.node_id == "bel_solitude_v2"
        assert planned.node.evidence_count == 4
        assert planned.node.belief_source_summary == "said so in March"

    def test_it_records_what_changed(self):
        planned = promote.next_version(
            {"_label": "BeliefNode", "node_id": "bel_x", "version": 1},
            statement="new wording",
            delta="he stopped apologising first",
            at=AT,
            took_ownership=False,
        )

        assert planned.node.version_delta == "he stopped apologising first"

    def test_a_third_version_follows_the_second(self):
        planned = promote.next_version(
            {"_label": "BeliefNode", "node_id": "bel_x_v2", "version": 2},
            statement="newer wording",
            delta="changed again",
            at=AT,
            took_ownership=False,
        )

        assert planned.node.node_id == "bel_x_v3"
        assert planned.node.previous_version_id == "bel_x_v2"

    def test_a_pattern_keeps_being_a_pattern(self):
        planned = promote.next_version(
            {
                "_label": "PatternNode",
                "node_id": "pat_x",
                "version": 1,
                "pattern_name": "Comparison spiral",
                "archetype_tags": '["seeker"]',
            },
            statement="a gentler version of the same habit",
            delta="less often now",
            at=AT,
            took_ownership=False,
        )

        assert planned.node_type == "PatternNode"
        assert planned.node.pattern_name == "Comparison spiral"
        assert planned.node.archetype_tags == ["seeker"]

    def test_taking_ownership_makes_the_idea_theirs(self):
        # Once somebody reworks a framing in their own words it is theirs,
        # and ranking it below their other thinking forever would be wrong.
        planned = promote.next_version(
            {
                "_label": "BeliefNode",
                "node_id": "bel_x",
                "version": 1,
                "provenance": "CO_CREATED",
                "verification_status": "UNVERIFIED",
            },
            statement="my own version of it now",
            delta="he made it his own",
            at=AT,
            took_ownership=True,
        )

        assert planned.node.provenance is Provenance.USER_GENERATED
        assert planned.node.verification_status is VerificationStatus.VERIFIED

    def test_without_ownership_the_origin_is_left_as_it_was(self):
        planned = promote.next_version(
            {
                "_label": "BeliefNode",
                "node_id": "bel_x",
                "version": 1,
                "provenance": "CO_CREATED",
                "verification_status": "UNVERIFIED",
            },
            statement="refined slightly",
            delta="small change",
            at=AT,
            took_ownership=False,
        )

        assert planned.node.provenance is Provenance.CO_CREATED


class TestQuestionsThatKeepComingBack:
    def test_a_question_asked_again_becomes_a_standing_one(
        self, make_item, make_candidate
    ):
        item = make_item(
            "Do I actually want this career?",
            observation_type=ObservationType.OPEN_LOOP,
            candidates=[make_candidate("obs_earlier", node_type="ObservationNode")],
        )

        planned = promote.build_open_loop(item, at=AT, exists=never_taken)

        assert planned is not None
        assert planned.node_type == "OpenLoopNode"
        assert planned.node.loop_description == "Do I actually want this career?"
        assert planned.node.source_episode_id == item.episode_id

    def test_a_question_asked_once_stays_a_note(self, make_item):
        # Whether a question is a standing investigation or a passing
        # thought is not something today's entry can say. Having come up
        # before is exactly what the search answered.
        item = make_item(
            "Do I actually want this?",
            observation_type=ObservationType.OPEN_LOOP,
            candidates=[],
        )

        assert promote.build_open_loop(item, at=AT, exists=never_taken) is None

    def test_other_kinds_of_finding_never_become_questions(
        self, make_item, make_candidate
    ):
        item = make_item(
            observation_type=ObservationType.PATTERN,
            candidates=[make_candidate("pat_a")],
        )

        assert promote.build_open_loop(item, at=AT, exists=never_taken) is None


class TestTheNewerHalfOfAContradiction:
    def test_it_knows_about_the_clash_from_the_start(self, make_item):
        planned = promote.build_contradicting_belief(
            make_item(observation_type=ObservationType.BELIEF),
            content("BELIEF", name="Thrives on attention"),
            contradiction_node_id="con_2026_06_11_001",
            at=AT,
            exists=never_taken,
        )

        assert planned.node.is_contradicted is True
        assert planned.node.contradiction_node_id == "con_2026_06_11_001"


class TestReadingWhatWasStored:
    """
    Records come back from the database with lists written as text, so
    building the next version of one has to cope with both forms.
    """

    def test_tags_stored_as_text_are_read_back(self):
        planned = promote.next_version(
            {
                "_label": "PatternNode",
                "node_id": "pat_x",
                "version": 1,
                "archetype_tags": '["seeker", "builder"]',
            },
            statement="same habit, gentler",
            delta="less often",
            at=AT,
            took_ownership=False,
        )

        assert planned.node.archetype_tags == ["seeker", "builder"]

    def test_tags_already_a_list_are_left_alone(self):
        planned = promote.next_version(
            {
                "_label": "PatternNode",
                "node_id": "pat_x",
                "version": 1,
                "archetype_tags": ["seeker"],
            },
            statement="same habit",
            delta="changed",
            at=AT,
            took_ownership=False,
        )

        assert planned.node.archetype_tags == ["seeker"]

    def test_unreadable_tags_are_dropped_rather_than_losing_the_version(self):
        # A version of a belief matters far more than a list of labels on it.
        planned = promote.next_version(
            {
                "_label": "PatternNode",
                "node_id": "pat_x",
                "version": 1,
                "archetype_tags": "[not json",
            },
            statement="same habit",
            delta="changed",
            at=AT,
            took_ownership=False,
        )

        assert planned.node.archetype_tags == []

    def test_no_tags_at_all_is_fine(self):
        planned = promote.next_version(
            {"_label": "PatternNode", "node_id": "pat_x", "version": 1},
            statement="same habit",
            delta="changed",
            at=AT,
            took_ownership=False,
        )

        assert planned.node.archetype_tags == []
