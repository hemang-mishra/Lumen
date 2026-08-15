"""
Tests for turning checked findings into graph nodes.

Three things are decided here and none of them are asked of the model:
what a node is called, when it happened, and when it was recorded. Each is
tested for the failure it would cause if it were got wrong — two episodes
claiming the same names, one entry smeared across a range of timestamps,
or a date the model invented becoming the basis of how recent something
looks forever after.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.pipeline.extraction.assembly import NodeFactory, strongest_signal
from lumen.pipeline.extraction.validation import (
    CleanChain,
    CleanEvent,
    CleanObservation,
    CleanStep,
)
from lumen.schemas.enums import (
    CausalStepType,
    EntryClass,
    ExtractionConfidence,
    NodeStatus,
    ObservationStatus,
    ObservationType,
    Provenance,
    SignalStrength,
    VerificationStatus,
)
from lumen.schemas.ids import SEMANTIC_ID_RE

RECORDED_AT = datetime(2026, 6, 11, 22, 30, tzinfo=UTC)


def finding(**overrides) -> CleanObservation:
    defaults = {
        "type": ObservationType.EMOTION,
        "content": "Felt small after seeing what Alex shipped",
        "provenance": Provenance.USER_GENERATED,
        "signal_strength": SignalStrength.STANDARD,
        "extraction_confidence": ExtractionConfidence.STANDARD,
    }
    return CleanObservation(**{**defaults, **overrides})


def happening(**overrides) -> CleanEvent:
    defaults = {
        "event_summary": "Ate at the cafe alone",
        "signal_strength": SignalStrength.STANDARD,
    }
    return CleanEvent(**{**defaults, **overrides})


def sequence(*steps: tuple[CausalStepType, str], **overrides) -> CleanChain:
    defaults = {
        "chain_summary": "comparison, then relief",
        "is_anticipatory": False,
        "steps": tuple(
            CleanStep(step_type=kind, content=content) for kind, content in steps
        ),
    }
    return CleanChain(**{**defaults, **overrides})


@pytest.fixture
def factory(make_extraction_input):
    """A factory over a plain reflective episode, with the clock held still."""

    def _build(payload=None, **kwargs):
        return NodeFactory(
            payload or make_extraction_input(**kwargs),
            extraction_model="fake-thinker",
            recorded_at=RECORDED_AT,
        )

    return _build


class TestNames:
    def test_a_node_name_says_the_date_episode_and_position(self, factory):
        nodes = factory().observations((finding(), finding()))

        assert [node.node_id for node in nodes] == [
            "obs_2026_06_11_01_001",
            "obs_2026_06_11_01_002",
        ]

    def test_each_kind_of_node_counts_separately(self, factory):
        built = factory()

        observations = built.observations((finding(),))
        events = built.events((happening(),))

        assert observations[0].node_id.endswith("_001")
        assert events[0].node_id.endswith("_001")
        assert observations[0].node_id != events[0].node_id

    def test_two_episodes_of_one_day_cannot_collide(self, factory):
        # Each episode is read by its own separate call and both start
        # counting at one, so without the episode number in the name the
        # second would claim names the first already used.
        first = factory(episode_index=1, total=2).observations((finding(), finding()))
        second = factory(episode_index=2, total=2).observations((finding(), finding()))

        assert not {node.node_id for node in first} & {node.node_id for node in second}

    def test_names_stay_in_the_readable_format(self, factory):
        built = factory()
        chains, steps = built.chains((sequence(
            (CausalStepType.TRIGGER, "saw the post"),
            (CausalStepType.OUTCOME, "let it go"),
        ),))
        nodes = [
            *built.observations((finding(),)),
            *built.events((happening(),)),
            *chains,
            *steps,
            built.session_anchor([]),
        ]

        for node in nodes:
            assert SEMANTIC_ID_RE.match(node.node_id), node.node_id


class TestTime:
    def test_every_node_shares_one_recording_time(self, factory):
        built = factory()
        chains, steps = built.chains((sequence(
            (CausalStepType.TRIGGER, "a"), (CausalStepType.OUTCOME, "b")
        ),))

        recorded = {
            node.created_at
            for node in [
                *built.observations((finding(), finding())),
                *built.events((happening(),)),
                *chains,
                *steps,
            ]
        }

        assert recorded == {RECORDED_AT}

    def test_the_experience_time_comes_from_the_entry(self, factory, make_extraction_input):
        payload = make_extraction_input()

        node = factory(payload).observations((finding(),))[0]

        assert node.occurred_at == payload.occurred_at
        assert node.occurred_at != node.created_at

    def test_the_clock_is_read_once_when_no_time_is_given(self, make_extraction_input):
        built = NodeFactory(make_extraction_input(), extraction_model="m")

        first = built.observations((finding(),))[0]
        second = built.observations((finding(),))[0]

        assert first.created_at == second.created_at


class TestFindings:
    def test_the_reading_model_is_recorded_on_every_finding(self, factory):
        node = factory().observations((finding(),))[0]

        assert node.extraction_model == "fake-thinker"
        assert node.extraction_attempt == 1

    def test_a_finding_from_a_close_reading_is_active(self, factory):
        node = factory().observations((finding(),))[0]

        assert node.status is ObservationStatus.ACTIVE

    def test_a_finding_from_a_thin_entry_is_marked_as_such(self, factory):
        # These go straight into the graph without ever being compared
        # against the person's history, so they have to be tellable apart
        # from findings that were.
        node = factory(entry_class=EntryClass.RAW_CAPTURE).observations((finding(),))[0]

        assert node.status is ObservationStatus.RAW_CAPTURE

    def test_quotes_and_names_are_carried_through(self, factory):
        node = factory().observations(
            (finding(person_refs=("Alex",), raw_evidence=("felt small",)),)
        )[0]

        assert node.person_refs == ["Alex"]
        assert node.raw_evidence == ["felt small"]

    def test_the_episode_is_recorded_on_every_node(self, factory, make_extraction_input):
        payload = make_extraction_input()
        built = factory(payload)
        chains, _ = built.chains((sequence(
            (CausalStepType.TRIGGER, "a"), (CausalStepType.OUTCOME, "b")
        ),))

        assert built.observations((finding(),))[0].episode_id == payload.episode.episode_id
        assert built.events((happening(),))[0].episode_id == payload.episode.episode_id
        assert chains[0].episode_id == payload.episode.episode_id


class TestWhoseIdeaItWas:
    def test_a_finding_is_the_persons_own_by_default(self, factory):
        node = factory().observations((finding(),))[0]

        assert node.provenance is Provenance.USER_GENERATED
        assert node.verification_status is VerificationStatus.IMPLICIT

    def test_a_finding_repeating_adopted_wording_is_shared(self, factory):
        node = factory(
            co_created_spans=["the comparing is the thing that hurts"]
        ).observations(
            (finding(content="The comparing is the thing that hurts, not the gap"),)
        )[0]

        assert node.provenance is Provenance.CO_CREATED
        assert node.verification_status is VerificationStatus.UNVERIFIED

    def test_adopted_wording_is_matched_in_the_quotes_too(self, factory):
        node = factory(co_created_spans=["a forcing function"]).observations(
            (finding(content="The gym works for me", raw_evidence=("a forcing function",)),)
        )[0]

        assert node.provenance is Provenance.CO_CREATED

    def test_unrelated_adopted_wording_changes_nothing(self, factory):
        node = factory(co_created_spans=["something else entirely"]).observations(
            (finding(),)
        )[0]

        assert node.provenance is Provenance.USER_GENERATED

    def test_a_finding_already_credited_as_shared_is_never_promoted(self, factory):
        # Raising its standing on a failed text match would quietly move the
        # assistant's ideas into the person's own history.
        node = factory().observations((finding(provenance=Provenance.CO_CREATED),))[0]

        assert node.provenance is Provenance.CO_CREATED

    def test_a_question_the_assistant_raised_stays_the_assistants(self, factory):
        node = factory().observations(
            (
                finding(
                    type=ObservationType.OPEN_LOOP,
                    provenance=Provenance.AI_GENERATED,
                    content="Is he staying out of meaning or out of fear?",
                ),
            )
        )[0]

        assert node.provenance is Provenance.AI_GENERATED


class TestSequences:
    def test_the_step_count_is_counted_not_believed(self, factory):
        chains, steps = factory().chains(
            (
                sequence(
                    (CausalStepType.TRIGGER, "saw the post"),
                    (CausalStepType.INTERNAL_STATE, "felt small"),
                    (CausalStepType.LESSON, "comparing is the problem"),
                ),
            )
        )

        assert chains[0].step_count == 3
        assert len(steps) == 3

    def test_steps_are_numbered_in_order_from_one(self, factory):
        _, steps = factory().chains(
            (
                sequence(
                    (CausalStepType.TRIGGER, "a"),
                    (CausalStepType.ACTION, "b"),
                    (CausalStepType.OUTCOME, "c"),
                ),
            )
        )

        assert [step.step_index for step in steps] == [1, 2, 3]

    def test_every_step_points_at_its_own_sequence(self, factory):
        pair = (
            sequence((CausalStepType.TRIGGER, "a"), (CausalStepType.OUTCOME, "b")),
            sequence((CausalStepType.TRIGGER, "c"), (CausalStepType.OUTCOME, "d")),
        )

        chains, steps = factory().chains(pair)

        assert {step.chain_id for step in steps} == {chains[0].node_id, chains[1].node_id}
        assert [step.chain_id for step in steps[:2]] == [chains[0].node_id] * 2

    def test_a_feared_sequence_is_marked_as_not_having_happened(self, factory):
        chains, _ = factory().chains(
            (
                sequence(
                    (CausalStepType.TRIGGER, "placement season"),
                    (CausalStepType.OUTCOME, "I fail"),
                    is_anticipatory=True,
                ),
            )
        )

        assert chains[0].is_anticipatory is True

    def test_nothing_is_built_from_no_sequences(self, factory):
        chains, steps = factory().chains(())

        assert (chains, steps) == ([], [])


class TestTheAnchor:
    def test_the_anchor_reuses_the_summary_already_written(
        self, factory, make_extraction_input
    ):
        payload = make_extraction_input()

        anchor = factory(payload).session_anchor([])

        assert anchor.session_summary == payload.episode.episode_summary

    def test_the_anchor_carries_the_entrys_date_and_label(
        self, factory, make_extraction_input
    ):
        payload = make_extraction_input(session_label="B")

        anchor = factory(payload).session_anchor([])

        assert anchor.event_date == payload.event_date
        assert anchor.session_label == "B"

    def test_an_unlabelled_entry_still_gets_a_label(self, factory):
        anchor = factory(session_label="").session_anchor([])

        assert anchor.session_label == "A"

    def test_the_anchor_takes_the_heaviest_weight_it_found(self, factory):
        built = factory()
        observations = built.observations(
            (
                finding(),
                finding(
                    type=ObservationType.METACOGNITIVE_BREAKTHROUGH,
                    signal_strength=SignalStrength.CRITICAL,
                ),
            )
        )

        anchor = built.session_anchor(observations)

        assert anchor.signal_strength is SignalStrength.CRITICAL

    def test_an_anchor_over_nothing_is_ordinary(self, factory):
        assert factory().session_anchor([]).signal_strength is SignalStrength.STANDARD

    def test_only_the_person_is_present_by_default(self, factory):
        assert factory().session_anchor([]).participant_entities == ["user"]

    def test_the_assistant_is_present_when_it_contributed(self, factory):
        anchor = factory(co_created_spans=["that framing"]).session_anchor([])

        assert anchor.participant_entities == ["user", "ai_facilitator"]

    def test_the_anchor_is_active_and_placed_in_time(self, factory, make_extraction_input):
        payload = make_extraction_input()

        anchor = factory(payload).session_anchor([])

        assert anchor.status is NodeStatus.ACTIVE
        assert anchor.occurred_at == payload.occurred_at
        assert anchor.created_at == RECORDED_AT


class TestWeighing:
    @pytest.mark.parametrize(
        "strengths, heaviest",
        [
            ([SignalStrength.STANDARD], SignalStrength.STANDARD),
            ([SignalStrength.STANDARD, SignalStrength.HIGH], SignalStrength.HIGH),
            ([SignalStrength.CRITICAL, SignalStrength.HIGH], SignalStrength.CRITICAL),
        ],
    )
    def test_the_heaviest_weight_wins(self, factory, strengths, heaviest):
        nodes = factory().observations(
            tuple(
                finding(type=ObservationType.CONTEXT, signal_strength=strength)
                for strength in strengths
            )
        )

        assert strongest_signal(nodes) is heaviest

    def test_nothing_found_weighs_nothing(self):
        assert strongest_signal([]) is SignalStrength.STANDARD
