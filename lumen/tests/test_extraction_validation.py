"""
Tests for the rules everything is checked against before it becomes
permanent.

Two properties are tested over and over here, because they are what the
whole design rests on.

The first is that a rule costs one item. A reply holding nine good
findings and one broken one must lose the one. Nearly every test below
therefore includes a valid sibling and asserts it survived.

The second is that nothing is ever filled in. A rule may reject an item or
straighten out something untidy, but it may never supply a missing piece,
because at this point in the pipeline a plausible guess and the truth are
indistinguishable.
"""

from __future__ import annotations

import pytest

from lumen.config import PipelineConfig
from lumen.pipeline.extraction.contracts import (
    DropRule,
    ExtractedCausalChain,
    ExtractedCausalStep,
    ExtractedEvent,
    ExtractedObservation,
    RawCaptureResponse,
    ReflectionExtractionResponse,
    RejectedItem,
)
from lumen.pipeline.extraction.validation import (
    build_context,
    flatten,
    validate_corrections,
    validate_raw_capture,
    validate_reflection,
)
from lumen.schemas.enums import (
    CausalStepType,
    ExtractionConfidence,
    ObservationType,
    Provenance,
    SignalStrength,
)
from lumen.schemas.pipeline import AmbiguousRef, CoreferenceMap, ResolvedEntity

TEXT = (
    "I went to the cafe alone today and ate there. "
    "Then I saw what Alex had shipped and felt small. "
    "The comparing is the thing that hurts."
)


def context_for(
    text: str = TEXT,
    *,
    raw_capture: bool = False,
    people: list[str] | None = None,
    ambiguous: list[tuple[str, list[str]]] | None = None,
    limits: PipelineConfig | None = None,
):
    """Build the facts the rules judge against."""
    coreference = CoreferenceMap(
        entry_id="sess_test_001",
        resolved_entities=[
            ResolvedEntity(
                span="he", resolved_to=name, confidence=0.9, resolution_basis="named"
            )
            for name in (people or [])
        ],
        ambiguous_refs=[
            AmbiguousRef(span=span, candidates=names, reason="two people nearby")
            for span, names in (ambiguous or [])
        ],
    )
    return build_context(
        episode_text=text,
        coreference_map=coreference,
        raw_capture=raw_capture,
        limits=limits or PipelineConfig(),
    )


def observation(**overrides) -> ExtractedObservation:
    """A finding that passes every rule, unless a test breaks one on purpose."""
    defaults = {
        "type": "EMOTION",
        "content": "Felt small after seeing what Alex shipped",
        "raw_evidence": ["felt small"],
    }
    return ExtractedObservation(**{**defaults, **overrides})


def valid_sibling() -> ExtractedObservation:
    """A second good finding, used to prove a rule cost only its own item."""
    return observation(
        type="LESSON",
        content="Comparing is what hurts, not the gap",
        raw_evidence=["The comparing is the thing that hurts"],
    )


def reply(*observations, events=(), chains=()) -> ReflectionExtractionResponse:
    return ReflectionExtractionResponse(
        observations=list(observations),
        events=list(events),
        causal_mechanisms=list(chains),
    )


def chain(*steps, summary: str = "comparison, then relief", **overrides):
    return ExtractedCausalChain(
        chain_summary=summary,
        causal_chain=[
            ExtractedCausalStep(step=index, type=kind, content=content)
            for index, (kind, content) in enumerate(steps, start=1)
        ],
        **overrides,
    )


def rules_fired(report) -> list[DropRule]:
    return [record.rule for record in report.drops]


class TestOneBadItemCostsOneItem:
    def test_an_invented_category_loses_only_that_finding(self):
        report = validate_reflection(
            reply(observation(type="VIBES"), valid_sibling()), context_for()
        )

        assert len(report.observations) == 1
        assert report.observations[0].type is ObservationType.LESSON
        assert rules_fired(report) == [DropRule.UNKNOWN_TYPE]

    def test_the_note_says_which_name_was_not_recognised(self):
        report = validate_reflection(reply(observation(type="VIBES")), context_for())

        assert report.drops[0].detail == "VIBES"

    def test_a_broken_chain_leaves_the_findings_alone(self):
        report = validate_reflection(
            reply(valid_sibling(), chains=[chain(("SOMETHING_ELSE", "x"), ("ACTION", "y"))]),
            context_for(),
        )

        assert len(report.observations) == 1
        assert report.chains == ()
        assert rules_fired(report) == [DropRule.UNKNOWN_STEP_TYPE]

    def test_a_broken_event_leaves_the_findings_alone(self):
        report = validate_reflection(
            reply(valid_sibling(), events=[ExtractedEvent(event_summary="  ")]),
            context_for(),
        )

        assert len(report.observations) == 1
        assert report.events == ()
        assert report.drops[0].item_kind == "event"


class TestFindingsAreRejected:
    def test_a_finding_with_no_content_goes(self):
        report = validate_reflection(reply(observation(content="   ")), context_for())

        assert report.observations == ()
        assert rules_fired(report) == [DropRule.EMPTY_CONTENT]

    def test_a_category_needing_audio_never_survives(self):
        # The pipeline only ever sees a transcript, so this cannot have been
        # measured. Letting it through would dress a guess as a reading.
        report = validate_reflection(
            reply(
                observation(
                    type="PROSODY_SIGNAL",
                    extraction_signal_strength="HIGH",
                )
            ),
            context_for(),
        )

        assert report.observations == ()
        assert rules_fired(report) == [DropRule.EXCLUDED_TYPE]

    @pytest.mark.parametrize(
        "field, bad_value",
        [
            ("provenance", "BORROWED"),
            ("extraction_signal_strength", "ENORMOUS"),
            ("extraction_confidence", "FUZZY"),
        ],
    )
    def test_an_unrecognised_setting_goes(self, field, bad_value):
        report = validate_reflection(
            reply(observation(**{field: bad_value}), valid_sibling()), context_for()
        )

        assert len(report.observations) == 1
        assert rules_fired(report) == [DropRule.UNKNOWN_ENUM_VALUE]
        assert field in report.drops[0].detail

    @pytest.mark.parametrize(
        "kind",
        [
            "SUPPRESSED_EMOTION_SURFACING",
            "METACOGNITIVE_INTERRUPT",
            "METACOGNITIVE_BREAKTHROUGH",
            "IDENTITY_FUSION_STATE",
            "EXISTENTIAL_REFLECTION",
        ],
    )
    def test_a_weighty_category_reported_as_ordinary_goes(self, kind):
        report = validate_reflection(
            reply(observation(type=kind, extraction_signal_strength="STANDARD")),
            context_for(),
        )

        assert report.observations == ()
        assert rules_fired(report) == [DropRule.SIGNAL_FLOOR]
        assert report.drops[0].detail == kind

    @pytest.mark.parametrize("strength", ["HIGH", "CRITICAL"])
    def test_the_same_category_survives_at_the_right_weight(self, strength):
        report = validate_reflection(
            reply(
                observation(
                    type="METACOGNITIVE_INTERRUPT", extraction_signal_strength=strength
                )
            ),
            context_for(),
        )

        assert len(report.observations) == 1
        assert report.drops == ()


class TestEventsAreRejected:
    def test_an_event_with_an_unrecognised_weight_goes(self):
        report = validate_reflection(
            reply(
                valid_sibling(),
                events=[
                    ExtractedEvent(event_summary="Ate at the cafe", signal_strength="HUGE")
                ],
            ),
            context_for(),
        )

        assert report.events == ()
        assert len(report.observations) == 1
        assert rules_fired(report) == [DropRule.UNKNOWN_ENUM_VALUE]

    def test_a_good_event_survives(self):
        report = validate_reflection(
            reply(
                events=[
                    ExtractedEvent(
                        event_summary="Ate at the cafe alone",
                        signal_strength="HIGH",
                        raw_evidence=["I went to the cafe alone today"],
                    )
                ]
            ),
            context_for(),
        )

        assert report.events[0].signal_strength is SignalStrength.HIGH
        assert report.drops == ()


class TestNamesAreNotInvented:
    def test_a_name_from_the_resolved_list_is_kept(self):
        report = validate_reflection(
            reply(observation(person_ref="Alex")), context_for(people=["Alex"])
        )

        assert report.observations[0].person_refs == ("Alex",)

    def test_a_name_appearing_only_in_the_text_is_kept(self):
        # Someone mentioned once by name never enters the resolved list,
        # because there was no pronoun for them to resolve.
        report = validate_reflection(reply(observation(person_ref="Alex")), context_for())

        assert report.observations[0].person_refs == ("Alex",)

    def test_a_name_from_an_unresolved_reference_is_kept(self):
        report = validate_reflection(
            reply(observation(person_ref="Rohan")),
            context_for(ambiguous=[("this guy", ["Alex", "Rohan"])]),
        )

        assert report.observations[0].person_refs == ("Rohan",)

    def test_an_invented_name_is_removed_but_the_finding_stays(self):
        # The statement is usually true and only the name is wrong. Losing a
        # real finding to shed a wrong name is the worse trade of the two.
        report = validate_reflection(
            reply(observation(person_ref="Priya")), context_for()
        )

        assert len(report.observations) == 1
        assert report.observations[0].person_refs == ()
        assert rules_fired(report) == [DropRule.UNKNOWN_PERSON]

    def test_the_note_never_repeats_the_name(self):
        report = validate_reflection(
            reply(observation(person_ref="Priya")), context_for()
        )

        assert "Priya" not in report.drops[0].detail

    def test_names_are_matched_ignoring_case(self):
        report = validate_reflection(
            reply(observation(person_ref="alex")), context_for(people=["Alex"])
        )

        assert report.observations[0].person_refs == ("alex",)

    def test_an_invented_name_on_an_event_is_removed_too(self):
        report = validate_reflection(
            reply(events=[ExtractedEvent(event_summary="Met up", person_refs=["Ghost"])]),
            context_for(),
        )

        assert report.events[0].person_refs == ()
        assert rules_fired(report) == [DropRule.UNKNOWN_PERSON]

    def test_a_blank_name_is_ignored_rather_than_reported(self):
        report = validate_reflection(reply(observation(person_ref="   ")), context_for())

        assert report.observations[0].person_refs == ()
        assert report.drops == ()


class TestChains:
    def test_a_good_sequence_survives_intact(self):
        report = validate_reflection(
            reply(chains=[chain(("TRIGGER", "saw the post"), ("OUTCOME", "felt small"))]),
            context_for(),
        )

        assert len(report.chains) == 1
        assert [step.step_type for step in report.chains[0].steps] == [
            CausalStepType.TRIGGER,
            CausalStepType.OUTCOME,
        ]

    def test_one_unreadable_step_takes_the_whole_sequence(self):
        # A sequence is a claim about order. Keeping the readable half would
        # quietly tell a shorter story than the one that was told.
        report = validate_reflection(
            reply(
                chains=[
                    chain(
                        ("TRIGGER", "saw the post"),
                        ("FEELING", "felt small"),
                        ("OUTCOME", "let it go"),
                    )
                ]
            ),
            context_for(),
        )

        assert report.chains == ()
        assert rules_fired(report) == [DropRule.UNKNOWN_STEP_TYPE]

    def test_a_step_with_no_content_takes_the_sequence_too(self):
        report = validate_reflection(
            reply(chains=[chain(("TRIGGER", "saw the post"), ("OUTCOME", "  "))]),
            context_for(),
        )

        assert report.chains == ()
        assert rules_fired(report) == [DropRule.EMPTY_CONTENT]

    def test_a_sequence_of_one_step_is_not_a_sequence(self):
        report = validate_reflection(
            reply(chains=[chain(("TRIGGER", "saw the post"))]), context_for()
        )

        assert report.chains == ()
        assert rules_fired(report) == [DropRule.CHAIN_TOO_SHORT]

    def test_a_sequence_with_no_summary_goes(self):
        report = validate_reflection(
            reply(chains=[chain(("TRIGGER", "a"), ("OUTCOME", "b"), summary="  ")]),
            context_for(),
        )

        assert report.chains == ()
        assert rules_fired(report) == [DropRule.EMPTY_CONTENT]

    def test_odd_numbering_is_straightened_out_rather_than_rejected(self):
        report = validate_reflection(
            reply(
                chains=[
                    ExtractedCausalChain(
                        chain_summary="out of order",
                        causal_chain=[
                            ExtractedCausalStep(step=7, type="OUTCOME", content="second"),
                            ExtractedCausalStep(step=3, type="TRIGGER", content="first"),
                        ],
                    )
                ]
            ),
            context_for(),
        )

        assert [step.content for step in report.chains[0].steps] == ["first", "second"]
        assert report.drops == ()

    def test_a_feared_sequence_keeps_its_flag(self):
        report = validate_reflection(
            reply(
                chains=[
                    chain(
                        ("TRIGGER", "placement season"),
                        ("OUTCOME", "I fail"),
                        is_anticipatory=True,
                    )
                ]
            ),
            context_for(),
        )

        assert report.chains[0].is_anticipatory is True

    def test_a_branch_marker_survives(self):
        report = validate_reflection(
            reply(
                chains=[
                    ExtractedCausalChain(
                        chain_summary="two outcomes",
                        causal_chain=[
                            ExtractedCausalStep(step=1, type="ACTION", content="slowed down"),
                            ExtractedCausalStep(
                                step=2, type="OUTCOME", content="calm", branch_id="a"
                            ),
                        ],
                    )
                ]
            ),
            context_for(),
        )

        assert report.chains[0].steps[1].branch_id == "a"


class TestCeilings:
    def test_too_many_findings_are_trimmed_and_counted(self):
        limits = PipelineConfig(max_observations_per_episode=2)

        report = validate_reflection(
            reply(observation(), observation(), observation(), observation()),
            context_for(limits=limits),
        )

        assert len(report.observations) == 2
        assert rules_fired(report) == [DropRule.OVER_LIMIT]
        assert report.drops[0].detail == "kept 2 of 4"

    def test_too_many_sequences_are_trimmed(self):
        limits = PipelineConfig(max_causal_chains_per_episode=1)
        one = chain(("TRIGGER", "a"), ("OUTCOME", "b"))

        report = validate_reflection(
            reply(chains=[one, one, one]), context_for(limits=limits)
        )

        assert len(report.chains) == 1
        assert rules_fired(report) == [DropRule.OVER_LIMIT]

    def test_an_overlong_sequence_is_trimmed_not_dropped(self):
        limits = PipelineConfig(max_causal_steps_per_chain=3)

        report = validate_reflection(
            reply(
                chains=[
                    chain(
                        ("TRIGGER", "a"),
                        ("INTERNAL_STATE", "b"),
                        ("ACTION", "c"),
                        ("OUTCOME", "d"),
                        ("LESSON", "e"),
                    )
                ]
            ),
            context_for(limits=limits),
        )

        assert len(report.chains[0].steps) == 3
        assert rules_fired(report) == [DropRule.OVER_LIMIT]

    def test_trimming_a_sequence_below_two_steps_drops_it(self):
        limits = PipelineConfig(max_causal_steps_per_chain=1)

        report = validate_reflection(
            reply(chains=[chain(("TRIGGER", "a"), ("OUTCOME", "b"))]),
            context_for(limits=limits),
        )

        assert report.chains == ()
        assert rules_fired(report) == [DropRule.OVER_LIMIT, DropRule.CHAIN_TOO_SHORT]


class TestEvidence:
    def test_a_quoted_finding_counts_as_grounded(self):
        report = validate_reflection(
            reply(observation(raw_evidence=["I went to the cafe alone today"])),
            context_for(),
        )

        assert report.observations[0].grounded is True
        assert report.ungrounded == 0

    def test_punctuation_and_spacing_differences_do_not_matter(self):
        report = validate_reflection(
            reply(observation(raw_evidence=["  I  went to the CAFE, alone today!  "])),
            context_for(),
        )

        assert report.observations[0].grounded is True

    def test_an_unquotable_finding_is_kept_but_counted(self):
        # A translated entry is legitimately quoted in words the person never
        # literally used, so this cannot be a rejection. The count is what
        # makes a rising rate of invention visible.
        report = validate_reflection(
            reply(observation(raw_evidence=["something they never said"])),
            context_for(),
        )

        assert len(report.observations) == 1
        assert report.observations[0].grounded is False
        assert report.ungrounded == 1

    def test_a_finding_with_no_quotes_at_all_is_counted(self):
        report = validate_reflection(reply(observation(raw_evidence=[])), context_for())

        assert report.ungrounded == 1

    def test_one_matching_quote_is_enough(self):
        report = validate_reflection(
            reply(observation(raw_evidence=["invented", "felt small"])), context_for()
        )

        assert report.observations[0].grounded is True

    def test_events_are_checked_the_same_way(self):
        report = validate_reflection(
            reply(
                events=[
                    ExtractedEvent(
                        event_summary="Ate at the cafe", raw_evidence=["never said this"]
                    )
                ]
            ),
            context_for(),
        )

        assert report.ungrounded == 1
        assert report.events[0].grounded is False


class TestDefaults:
    def test_a_finding_is_the_persons_own_unless_stated(self):
        report = validate_reflection(reply(observation()), context_for())

        assert report.observations[0].provenance is Provenance.USER_GENERATED
        assert report.observations[0].signal_strength is SignalStrength.STANDARD
        assert (
            report.observations[0].extraction_confidence is ExtractionConfidence.STANDARD
        )

    def test_a_distant_memory_can_be_marked_as_reconstructed(self):
        report = validate_reflection(
            reply(observation(extraction_confidence="RECONSTRUCTIVE")), context_for()
        )

        assert (
            report.observations[0].extraction_confidence
            is ExtractionConfidence.RECONSTRUCTIVE
        )

    def test_settings_are_read_regardless_of_case_or_spacing(self):
        report = validate_reflection(
            reply(observation(type=" emotion ", extraction_signal_strength="high")),
            context_for(),
        )

        assert report.observations[0].type is ObservationType.EMOTION
        assert report.observations[0].signal_strength is SignalStrength.HIGH


class TestTheThinPath:
    def test_the_topic_is_kept(self):
        report = validate_raw_capture(
            RawCaptureResponse(context="Mentions going to a cafe"),
            context_for(raw_capture=True),
        )

        assert len(report.observations) == 1
        assert report.observations[0].type is ObservationType.CONTEXT

    def test_a_feeling_the_person_named_is_kept(self):
        report = validate_raw_capture(
            RawCaptureResponse(
                context="Mentions Alex",
                emotion="small",
                emotion_quote="felt small",
            ),
            context_for(raw_capture=True),
        )

        assert [item.type for item in report.observations] == [
            ObservationType.CONTEXT,
            ObservationType.EMOTION,
        ]
        assert report.observations[1].raw_evidence == ("felt small",)

    def test_a_feeling_with_no_quote_is_refused(self):
        report = validate_raw_capture(
            RawCaptureResponse(context="Mentions Alex", emotion="anxious"),
            context_for(raw_capture=True),
        )

        assert [item.type for item in report.observations] == [ObservationType.CONTEXT]
        assert rules_fired(report) == [DropRule.QUOTE_NOT_FOUND]

    def test_a_feeling_quoting_words_that_are_not_there_is_refused(self):
        # This is the whole point of the path: a tired-sounding entry is not
        # the same as someone saying they are tired.
        report = validate_raw_capture(
            RawCaptureResponse(
                context="Mentions Alex",
                emotion="exhausted",
                emotion_quote="I am completely exhausted",
            ),
            context_for(raw_capture=True),
        )

        assert len(report.observations) == 1
        assert report.drops[0].detail == "quote not in entry"

    def test_no_feeling_reported_is_fine(self):
        report = validate_raw_capture(
            RawCaptureResponse(context="Mentions a cafe"), context_for(raw_capture=True)
        )

        assert len(report.observations) == 1
        assert report.drops == ()

    def test_an_empty_topic_is_recorded_as_missing(self):
        report = validate_raw_capture(
            RawCaptureResponse(context="   "), context_for(raw_capture=True)
        )

        assert report.observations == ()
        assert rules_fired(report) == [DropRule.EMPTY_CONTENT]

    def test_the_thin_path_forbids_every_deeper_category(self):
        report = validate_reflection(
            reply(observation(type="CORE_WOUND"), observation(type="CONTEXT")),
            context_for(raw_capture=True),
        )

        assert [item.type for item in report.observations] == [ObservationType.CONTEXT]
        assert rules_fired(report) == [DropRule.TYPE_NOT_ALLOWED_HERE]


class TestWhatIsKeptForAskingAgain:
    def test_a_lost_item_is_kept_whole(self):
        # Something has to be able to show the model what it got wrong.
        report = validate_reflection(
            reply(observation(type="VIBES", content="the comparing hurts")),
            context_for(),
        )

        assert len(report.rejected) == 1
        assert report.rejected[0].payload.content == "the comparing hurts"
        assert report.rejected[0].attempts == 1

    def test_the_note_beside_it_holds_none_of_the_words(self):
        # The two records describe the same event and only one of them is
        # ever written to a log.
        report = validate_reflection(
            reply(observation(type="VIBES", content="a very private sentence")),
            context_for(),
        )

        assert "private" not in report.drops[0].detail
        assert "private" not in report.drops[0].item_kind

    def test_an_item_that_only_lost_a_name_is_not_rejected(self):
        report = validate_reflection(
            reply(observation(person_ref="Priya")), context_for()
        )

        assert len(report.observations) == 1
        assert report.rejected == ()
        assert len(report.drops) == 1

    def test_trimming_a_list_rejects_nothing(self):
        limits = PipelineConfig(max_observations_per_episode=1)

        report = validate_reflection(
            reply(observation(), observation()), context_for(limits=limits)
        )

        assert report.rejected == ()

    def test_the_kind_of_each_lost_item_is_recorded(self):
        report = validate_reflection(
            reply(
                observation(type="VIBES"),
                events=[ExtractedEvent(event_summary="went out", signal_strength="HUGE")],
                chains=[chain(("TRIGGER", "a"))],
            ),
            context_for(),
        )

        assert {item.item_kind for item in report.rejected} == {
            "observation",
            "event",
            "chain",
        }

    def test_nothing_is_kept_from_a_thin_entry(self):
        # Nothing here is ever asked about again, so nothing needs keeping.
        report = validate_raw_capture(
            RawCaptureResponse(context="a cafe", emotion="tired", emotion_quote="nope"),
            context_for(raw_capture=True),
        )

        assert report.rejected == ()
        assert len(report.drops) == 1


class TestJudgingCorrections:
    def outstanding(self, *rules_and_items):
        return tuple(
            RejectedItem(item_kind=kind, index=index, rule=rule, payload=payload)
            for index, (kind, rule, payload) in enumerate(rules_and_items)
        )

    def test_a_corrected_item_is_accepted(self):
        outstanding = self.outstanding(
            ("observation", DropRule.UNKNOWN_TYPE, ExtractedObservation(type="VIBES", content="x"))
        )

        report = validate_corrections(
            reply(observation(type="PATTERN", content="a real pattern")),
            context_for(),
            outstanding=outstanding,
        )

        assert len(report.observations) == 1
        assert report.rejected == ()

    def test_it_is_held_to_exactly_the_same_rules(self):
        # There is no gentler second pass. A rule that can be worn down by
        # being asked twice is not a rule.
        outstanding = self.outstanding(
            ("observation", DropRule.UNKNOWN_TYPE, ExtractedObservation(type="VIBES", content="x"))
        )

        report = validate_corrections(
            reply(observation(type="METACOGNITIVE_INTERRUPT", extraction_signal_strength="STANDARD")),
            context_for(),
            outstanding=outstanding,
        )

        assert report.observations == ()
        assert report.rejected[0].last_rule is DropRule.SIGNAL_FLOOR

    def test_the_original_problem_is_never_overwritten(self):
        outstanding = self.outstanding(
            ("observation", DropRule.UNKNOWN_TYPE, ExtractedObservation(type="VIBES", content="x"))
        )

        report = validate_corrections(
            reply(observation(type="METACOGNITIVE_INTERRUPT", extraction_signal_strength="STANDARD")),
            context_for(),
            outstanding=outstanding,
        )

        assert report.rejected[0].rule is DropRule.UNKNOWN_TYPE
        assert report.rejected[0].attempts == 2

    def test_an_item_left_out_of_the_answer_stays_outstanding(self):
        # Leaving it out is an answer the correction explicitly permits.
        outstanding = self.outstanding(
            ("observation", DropRule.UNKNOWN_TYPE, ExtractedObservation(type="VIBES", content="x"))
        )

        report = validate_corrections(reply(), context_for(), outstanding=outstanding)

        assert report.observations == ()
        assert report.rejected[0].last_rule is DropRule.NOT_CORRECTED

    def test_corrections_are_matched_by_their_order(self):
        outstanding = self.outstanding(
            ("observation", DropRule.UNKNOWN_TYPE, ExtractedObservation(type="A", content="first")),
            ("observation", DropRule.UNKNOWN_TYPE, ExtractedObservation(type="B", content="second")),
        )

        report = validate_corrections(
            reply(
                observation(type="PATTERN", content="fixed first"),
                observation(type="VIBES", content="still broken"),
            ),
            context_for(),
            outstanding=outstanding,
        )

        assert [item.content for item in report.observations] == ["fixed first"]
        assert report.rejected[0].payload.content == "second"

    def test_extra_items_in_an_answer_are_ignored(self):
        # The correction asked for one thing. Anything beyond that was not
        # asked for and has nothing to be matched against.
        outstanding = self.outstanding(
            ("observation", DropRule.UNKNOWN_TYPE, ExtractedObservation(type="VIBES", content="x"))
        )

        report = validate_corrections(
            reply(
                observation(type="PATTERN", content="the one asked for"),
                observation(type="BELIEF", content="an extra one"),
            ),
            context_for(),
            outstanding=outstanding,
        )

        assert len(report.observations) == 1

    def test_each_kind_is_matched_within_its_own_group(self):
        outstanding = self.outstanding(
            ("observation", DropRule.UNKNOWN_TYPE, ExtractedObservation(type="VIBES", content="x")),
            ("event", DropRule.EMPTY_CONTENT, ExtractedEvent(event_summary="")),
        )

        report = validate_corrections(
            ReflectionExtractionResponse(
                observations=[observation(type="PATTERN", content="fixed")],
                events=[ExtractedEvent(event_summary="Ate at the cafe")],
            ),
            context_for(),
            outstanding=outstanding,
        )

        assert len(report.observations) == 1
        assert len(report.events) == 1
        assert report.rejected == ()

    def test_a_corrected_sequence_is_rebuilt(self):
        outstanding = self.outstanding(
            (
                "chain",
                DropRule.UNKNOWN_STEP_TYPE,
                ExtractedCausalChain(chain_summary="a to b"),
            )
        )

        report = validate_corrections(
            reply(chains=[chain(("TRIGGER", "saw the post"), ("OUTCOME", "let it go"))]),
            context_for(),
            outstanding=outstanding,
        )

        assert len(report.chains) == 1
        assert len(report.chains[0].steps) == 2


class TestTextFlattening:
    def test_text_is_reduced_to_padded_lowercase_words(self):
        assert flatten("Hello,   World!") == " hello world "

    def test_text_with_nothing_in_it_stays_empty(self):
        assert flatten("  ...  ").strip() == ""

    def test_a_missing_category_reads_as_unrecognised(self):
        report = validate_reflection(reply(observation(type="")), context_for())

        assert rules_fired(report) == [DropRule.UNKNOWN_TYPE]

    def test_a_name_of_only_punctuation_is_not_a_person(self):
        report = validate_reflection(reply(observation(person_ref="...")), context_for())

        assert report.observations[0].person_refs == ()
        assert rules_fired(report) == [DropRule.UNKNOWN_PERSON]

    def test_a_quote_of_only_punctuation_supports_nothing(self):
        report = validate_raw_capture(
            RawCaptureResponse(context="Mentions a cafe", emotion="flat", emotion_quote="--"),
            context_for(raw_capture=True),
        )

        assert len(report.observations) == 1
        assert rules_fired(report) == [DropRule.QUOTE_NOT_FOUND]

    def test_a_word_is_matched_whole_not_as_a_fragment(self):
        # "Al" must not match inside "Alex", or half the alphabet becomes a
        # known person.
        report = validate_reflection(reply(observation(person_ref="Al")), context_for())

        assert report.observations[0].person_refs == ()
