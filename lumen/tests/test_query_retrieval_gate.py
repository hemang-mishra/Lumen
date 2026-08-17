"""
Holding back the records that must not arrive uninvited.

The rule is inverted from what "high signal" usually means, and that
inversion is the thing worth pinning down: the heaviest records are the ones
most carefully withheld, not the ones surfaced first.

Two of these tests cover cases the specification left open — which areas of
life count as sensitive, and what to do with a heavy record that names no
area at all, which is most of them.
"""

from __future__ import annotations

from lumen.query.retrieval import gate
from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import Domain, RetrievalPass, SignalStrength


def record(
    node_id: str = "pat_1",
    *,
    signal: SignalStrength = SignalStrength.CRITICAL,
    domain: Domain | None = Domain.SELF_CONCEPT,
) -> RetrievedNode:
    """One candidate, with only the two fields the gate reads set."""
    return RetrievedNode(
        node_id=node_id,
        node_type="PatternNode",
        preview="something heavy",
        found_by=RetrievalPass.SEMANTIC,
        similarity=0.9,
        signal_strength=signal,
        domain=domain,
        rank_score=1.8,
    )


class TestWhatIsHeldBack:
    def test_the_heaviest_records_wait_for_an_invitation(self):
        kept, withheld = gate.apply([record()], unlocked=())

        assert kept == []
        assert withheld == ("pat_1",)

    def test_once_the_person_opens_the_subject_it_is_offered(self):
        # Reading the turn detects that and records it on the day; this is
        # the first thing that acts on it.
        kept, withheld = gate.apply([record()], unlocked=(Domain.SELF_CONCEPT,))

        assert [node.node_id for node in kept] == ["pat_1"]
        assert withheld == ()

    def test_opening_a_different_subject_does_not_unlock_this_one(self):
        kept, _ = gate.apply([record()], unlocked=(Domain.CAREER,))

        assert kept == []

    def test_an_ordinary_record_is_never_gated(self):
        kept, withheld = gate.apply(
            [record(signal=SignalStrength.HIGH)], unlocked=()
        )

        assert [node.node_id for node in kept] == ["pat_1"]
        assert withheld == ()

    def test_a_heavy_record_about_an_everyday_subject_is_not_gated(self):
        # Gating everything CRITICAL would gate most of what makes the
        # system useful. Only the sensitive areas need the invitation.
        kept, _ = gate.apply([record(domain=Domain.CAREER)], unlocked=())

        assert [node.node_id for node in kept] == ["pat_1"]

    def test_what_survived_keeps_its_order(self):
        kept, _ = gate.apply(
            [record("pat_a", signal=SignalStrength.STANDARD), record("pat_b")],
            unlocked=(),
        )

        assert [node.node_id for node in kept] == ["pat_a"]


class TestARecordThatNamesNoArea:
    # Individual notes record no area of life at all — only the standing
    # beliefs and patterns do — so this is the common case rather than an
    # exotic one.

    def test_it_is_treated_as_sensitive_by_default(self):
        kept, withheld = gate.apply([record(domain=None)], unlocked=())

        assert kept == []
        assert withheld == ("pat_1",)

    def test_any_opened_sensitive_subject_releases_it(self):
        # The person has shown they are on that ground; a heavy record whose
        # subject is unrecorded is more likely to belong there than not.
        kept, _ = gate.apply([record(domain=None)], unlocked=(Domain.HEALTH,))

        assert [node.node_id for node in kept] == ["pat_1"]

    def test_an_everyday_subject_does_not_release_it(self):
        kept, _ = gate.apply([record(domain=None)], unlocked=(Domain.CAREER,))

        assert kept == []


class TestWhichSubjectsAreSensitive:
    def test_the_list_is_the_four_that_were_chosen(self):
        assert gate.SENSITIVE_DOMAINS == frozenset(
            {
                Domain.SELF_CONCEPT,
                Domain.RELATIONAL,
                Domain.HEALTH,
                Domain.SPIRITUALITY,
            }
        )

    def test_emotional_is_deliberately_not_one_of_them(self):
        # In a conversation of this kind nearly everything is emotional.
        # Gating that would gate the whole graph, which is useless rather
        # than careful.
        assert Domain.EMOTIONAL not in gate.SENSITIVE_DOMAINS

        kept, _ = gate.apply([record(domain=Domain.EMOTIONAL)], unlocked=())

        assert [node.node_id for node in kept] == ["pat_1"]
