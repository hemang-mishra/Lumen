"""
The one object that is allowed to answer a review question.

Everything else in this package is a function taking a graph or a store as a
parameter, which is right for testing and wrong for the web layer. A route
handed a writable graph can do anything at all to somebody's history; a route
handed one of these can list questions, answer one, defer one, or run the
housekeeping — and nothing else. The stores and the models live inside and
are never handed out.

Writing goes out through the same path the pipeline uses. There is one way
into the graph in this system, and a review answer is not an exception to it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from lumen.config import AppConfig
from lumen.operational.enums import OPEN_HITL_STATUSES, HitlItemStatus
from lumen.operational.repositories import (
    IllegalStateTransitionError,
    OperationalStore,
    RecordNotFoundError,
)
from lumen.operational.schemas import HitlQueueItemRecord
from lumen.pipeline.orchestration import commit as writing
from lumen.pipeline.orchestration.embed import prepare_index, text_for_index
from lumen.providers.protocols import EmbeddingProvider
from lumen.review import cards, housekeeping, resolve
from lumen.review.contracts import (
    ChoiceNotOffered,
    QueueCard,
    QueueCounts,
    QueueView,
    ResolutionChoice,
    ResolutionOutcome,
    ReviewError,
    SweepReport,
)
from lumen.schemas.enums import HitlResolutionChoice, ReconciliationAction
from lumen.schemas.pipeline import FrozenProposal
from lumen.stores import StoreRegistry, UserStores

logger = logging.getLogger(__name__)

# The answers that mean "no". Both can come to mean "leave it alone" rather
# than "do the other thing", depending on what was recommended.
_REFUSALS: frozenset[ResolutionChoice] = frozenset(
    {ResolutionChoice.REJECT, ResolutionChoice.CREATE_NEW}
)

# A limit high enough to mean "all of them" when sweeping. The queue is
# capped in the tens; this is a guard against a runaway read, not a page.
_EVERYTHING = 10_000


class MissingProposal(ReviewError):
    """
    There is a question in the queue but nothing recorded to answer it with.

    Only reachable for items raised before the system started keeping what it
    was going to write. Refused rather than improvised: inventing a change
    now would write something to somebody's history that nobody proposed.
    """

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(
            f"review item {item_id!r} has nothing recorded to carry out"
        )


class ReviewService:
    """
    Lists the questions waiting for somebody, and carries out their answers.

    Safe to call from more than one place at once. Answering is serialised on
    a lock, because two taps on the same card would each find it unanswered
    and both write the change.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        stores: StoreRegistry,
        ops: OperationalStore,
        open_embedder: Callable[[], EmbeddingProvider],
    ) -> None:
        """
        Args:
            config: Settings for this deployment.
            stores: Where a person's graph and search index are borrowed
                from. Not a graph, because there is one per person and this
                object serves all of them — which of them a call is about is
                decided by the identifier that call was given.
            ops: The operational store, holding the queue itself. Shared, and
                already keyed by person on every row.
            open_embedder: How to reach the embedding model, asked for only
                when an answer creates a record that has to be findable.
                Taken as a way of getting one rather than the thing itself,
                since a deployment with no model still lists a queue.
        """
        self._config = config
        self._stores = stores
        self._ops = ops
        self._open_embedder = open_embedder
        self._lock = threading.Lock()

    # -- reading ---------------------------------------------------------

    def list_queue(self, user_id: str, *, limit: int = 20) -> QueueView:
        """
        The questions to put to somebody now, in the order to ask them.

        Housekeeping runs first, so opening the queue is also what keeps it
        honest: anything that ran out of time is settled and anything parked
        outside is let in before the page is drawn.
        """
        self.sweep(user_id)
        now = _now()
        items = self._ops.hitl.list_visible(user_id, now=now, limit=limit)
        with self._stores.lease(user_id) as stores:
            drawn = self._cards_for(items, now=now, stores=stores)
        return QueueView(cards=drawn, counts=self.counts(user_id))

    def get_card(self, user_id: str, item_id: str) -> QueueCard:
        """One question in full. Does no housekeeping — it only reads."""
        item = self._owned(user_id, item_id)
        with self._stores.lease(user_id) as stores:
            return self._cards_for([item], now=_now(), stores=stores)[0]

    def counts(self, user_id: str) -> QueueCounts:
        """
        How much is waiting, for a badge.

        Deliberately does no housekeeping. This is polled from every screen
        in the application, and a count that quietly settles things as a side
        effect of being displayed is a count nobody should trust.
        """
        cap = self._config.operational.hitl_queue_cap
        asked = self._ops.hitl.count_asked(user_id)
        parked = len(self._ops.hitl.list_parked(user_id))
        visible = len(self._ops.hitl.list_visible(user_id, now=_now(), limit=cap))
        return QueueCounts(
            pending=self._ops.hitl.count_pending(user_id),
            visible=visible,
            parked=parked,
            cap=cap,
            at_capacity=asked >= cap,
            oldest_asked_at=self._ops.hitl.oldest_pending_at(user_id),
        )

    # -- answering -------------------------------------------------------

    def resolve(
        self,
        user_id: str,
        item_id: str,
        choice: ResolutionChoice,
        *,
        recorded_choice: HitlResolutionChoice | None = None,
    ) -> ResolutionOutcome:
        """
        Carry out one answer, for real.

        Deferring is not one of these. It changes nothing in the graph and
        has its own call, so the code that writes never has to hold a case
        where it writes nothing on purpose.
        """
        if choice is ResolutionChoice.SNOOZE:
            raise ChoiceNotOffered(
                choice.value,
                [ResolutionChoice.APPROVE.value, ResolutionChoice.REJECT.value],
            )

        with self._lock, self._stores.lease(user_id) as stores:
            outcome = self._resolve_one(
                user_id,
                item_id,
                choice,
                recorded_choice=recorded_choice,
                stores=stores,
            )

        # Answering makes room, so whatever was parked behind this can come
        # in. Done outside the lock: it settles nothing and writes no graph.
        admitted = self.sweep(user_id).admitted
        return outcome.model_copy(update={"admitted": admitted})

    def snooze(self, user_id: str, item_id: str) -> QueueCard:
        """
        Put a question off, and hide it while it waits.

        Deferring is also what makes an item capable of settling itself
        later. Nothing that has never been looked at ever does.
        """
        item = self._owned(user_id, item_id)
        now = _now()
        hours = self._config.operational.hitl_snooze_hours
        self._ops.hitl.snooze(item.id, until=now + timedelta(hours=hours), at=now)
        return self.get_card(user_id, item_id)

    def sweep(self, user_id: str) -> SweepReport:
        """
        Run the housekeeping: settle what has expired, admit what is parked.

        Exposed on its own as well as run automatically, so a scheduler has
        exactly one thing to call and a person has one thing to press.
        """
        # Closing these first, because it frees room under the ceiling for
        # whatever is parked behind them.
        closed = self._close_pointless(user_id)
        report = housekeeping.sweep(
            user_id,
            ops=self._ops,
            resolver=lambda item_id: self._settle_unanswered(user_id, item_id),
            cap=self._config.operational.hitl_queue_cap,
            auto_resolve_days=self._config.operational.hitl_auto_resolve_days,
            now=_now(),
        )
        return report.model_copy(update={"closed": closed})

    def dismiss(self, user_id: str, item_id: str) -> ResolutionOutcome:
        """
        Withdraw a question that can no longer be answered.

        Only for a question no answer could change anything about — either
        nothing was recorded to carry out, or everything that was writes the
        same nothing. Refused for anything with a real decision behind it,
        because "I do not want to decide this" is what deferring is for, and
        a question with consequences should never be quietly dropped.

        Nothing is written to the history. The note is stamped as withdrawn,
        so the graph still shows that Lumen hesitated here and that the
        question was dropped rather than settled.
        """
        item = self._owned(user_id, item_id)
        if item.status not in OPEN_HITL_STATUSES:
            raise IllegalStateTransitionError(
                f"review item {item_id!r} was already settled as {item.status.value}"
            )
        if self._can_change_anything(item):
            raise ChoiceNotOffered(
                "DISMISS", [ResolutionChoice.APPROVE.value, ResolutionChoice.REJECT.value]
            )

        with self._lock, self._stores.lease(user_id) as stores:
            return self._close_without_acting(
                item,
                stores=stores,
                choice=ResolutionChoice.REJECT,
                recorded_choice=HitlResolutionChoice.DISMISSED_UNANSWERABLE,
            )

    def _close_without_acting(
        self,
        item: HitlQueueItemRecord,
        *,
        stores: UserStores,
        choice: ResolutionChoice,
        recorded_choice: HitlResolutionChoice,
    ) -> ResolutionOutcome:
        """
        Settle a question by writing nothing at all.

        Two answers end here and they mean different things — the person
        turned the suggestion down, or the question could no longer be
        answered — so the recorded choice tells them apart. What they share
        is the outcome: the finding stays exactly as it was, part of the
        entry it came from and nothing more, and the note stops claiming
        somebody is going to look at it.

        Takes no lock of its own; both callers hold it already. The lock is
        not reentrant, so taking it again here deadlocks rather than failing
        in any way a reader would notice.
        """
        stores.graph.dismiss_decision(item.audit_node_id, at=_now())
        self._ops.hitl.update_status(
            item.id,
            _settled_as(recorded_choice),
            resolution_choice=recorded_choice,
        )

        logger.info(
            "review item closed without acting",
            extra={"item_id": item.id, "choice": recorded_choice.value},
        )
        return ResolutionOutcome(
            item_id=item.id,
            choice=choice,
            recorded_choice=recorded_choice,
            action_taken=ReconciliationAction.AMBIGUOUS,
            original_audit_node_id=item.audit_node_id,
            new_audit_node_id="",
            writes_nothing=True,
        )

    def _can_change_anything(self, item: HitlQueueItemRecord) -> bool:
        """
        Whether answering this question could alter the history at all.

        A question with nothing saved behind it cannot, and neither can one
        whose every saved answer writes the same nothing. The two arrive by
        different routes and come to the same place: there is no decision
        here to protect.
        """
        try:
            return self._proposal_for(item).can_change_anything
        except MissingProposal:
            return False

    def _is_provably_pointless(self, item: HitlQueueItemRecord) -> bool:
        """
        Whether it is *known* that no answer to this could change anything.

        Deliberately not the same as "cannot be answered". A question with
        nothing saved behind it also changes nothing, but for a different
        reason — the working was lost, not absent — and it may well have been
        a real question. That one is shown and withdrawn on purpose. This one
        is closed without asking, so it has to be provable rather than
        merely likely.
        """
        try:
            return not self._proposal_for(item).can_change_anything
        except MissingProposal:
            return False

    def _close_pointless(self, user_id: str) -> list[str]:
        """
        Close the questions that were asked before it was clear they were not
        questions.

        The pipeline no longer raises these, but the ones already raised sit
        in the queue taking up room under the ceiling and asking for an
        answer that cannot matter. Closing them writes nothing to anybody's
        history — exactly as if they had never been asked.
        """
        closed: list[str] = []
        for item in self._ops.hitl.list_pending(user_id, limit=_EVERYTHING):
            if not self._is_provably_pointless(item):
                continue
            try:
                with self._lock, self._stores.lease(user_id) as stores:
                    self._close_without_acting(
                        item,
                        stores=stores,
                        choice=ResolutionChoice.REJECT,
                        recorded_choice=HitlResolutionChoice.DISMISSED_UNANSWERABLE,
                    )
            except Exception:
                logger.warning(
                    "could not close a question that could change nothing",
                    extra={"item_id": item.id},
                )
                continue
            closed.append(item.id)

        if closed:
            logger.info("closed questions with nothing to decide", extra={"closed": len(closed)})
        return closed

    def _settle_unanswered(self, user_id: str, item_id: str) -> ResolutionOutcome:
        """
        Close an item that ran out of time, without anybody answering it.

        The finding becomes its own separate thing — the same outcome as
        turning the suggestion down, and asked for by meaning rather than by
        name, because a tie's word for it is not a recommendation's. It is
        recorded as having expired, so the graph never claims somebody chose
        this.
        """
        item = self._owned(user_id, item_id)
        proposal = self._proposal_for(item)
        with self._stores.lease(user_id) as stores:
            return self._resolve_one(
                user_id,
                item_id,
                cards.standing_alone_choice(proposal),
                recorded_choice=HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE,
                stores=stores,
            )

    # -- the work --------------------------------------------------------

    def _resolve_one(
        self,
        user_id: str,
        item_id: str,
        choice: ResolutionChoice,
        *,
        recorded_choice: HitlResolutionChoice | None,
        stores: UserStores,
    ) -> ResolutionOutcome:
        """
        Work out one answer and write it.

        The queue row is only moved after the graph has taken the change. The
        other order would leave a question marked answered whose answer never
        landed, which nothing downstream could ever notice.
        """
        item = self._owned(user_id, item_id)
        # Checked before anything is written, not after. The store refuses a
        # second answer too, but by then the graph has already taken the
        # change — and the second copy of it fails on a duplicate identifier
        # rather than on the thing that is actually wrong.
        if item.status not in OPEN_HITL_STATUSES:
            raise IllegalStateTransitionError(
                f"review item {item_id!r} was already settled as {item.status.value}"
            )

        proposal = self._proposal_for(item)

        # Turning down "record this on its own" cannot mean doing it anyway.
        # Where saying no has no writing behind it, it is a refusal: the
        # finding stays with its entry and nothing is created.
        if choice in _REFUSALS and proposal.saying_no_means_doing_nothing:
            return self._close_without_acting(
                item,
                stores=stores,
                choice=choice,
                recorded_choice=recorded_choice or HitlResolutionChoice.DECLINED,
            )

        rows = cards.read_rows(
            cards.wanted_node_ids([proposal]), graph=stores.graph
        )

        plan = resolve.plan_resolution(
            proposal, choice, at=_now(), rows=rows, recorded_choice=recorded_choice
        )
        report = self._write(plan.write_plan, stores=stores)

        self._ops.hitl.update_status(
            item.id,
            _settled_as(plan.recorded_choice),
            resolution_choice=plan.recorded_choice,
            resolved_action=plan.action_taken,
        )

        logger.info(
            "review item answered",
            extra={
                "item_id": item.id,
                "choice": choice.value,
                "action": plan.action_taken.value,
            },
        )
        return ResolutionOutcome(
            item_id=item.id,
            choice=choice,
            recorded_choice=plan.recorded_choice,
            action_taken=plan.action_taken,
            original_audit_node_id=proposal.audit_node_id,
            new_audit_node_id=plan.new_audit.node_id,
            nodes_written=list(report.nodes_written),
            edges_written=list(report.edges_written),
            vectors_written=list(report.vectors_written),
            unindexed_node_ids=list(report.unindexed_node_ids),
            writes_nothing=plan.writes_nothing,
        )

    def _write(self, write_plan, *, stores: UserStores):
        """
        Save one answer's records, links and updates.

        A search index that refuses the new record is not treated as a
        failure of the answer. The graph is right and the decision is made;
        what is lost is that the new record cannot be found by meaning yet,
        which is repairable and is reported rather than hidden.
        """
        needs_indexing = any(
            text_for_index(planned) is not None for planned in write_plan.nodes
        )
        entries = (
            prepare_index(write_plan, embedder=self._open_embedder())
            if needs_indexing
            else []
        )

        try:
            return writing.commit(
                write_plan, entries, graph=stores.graph, vectors=stores.vectors
            )
        except writing.IndexWriteFailed as failure:
            logger.warning(
                "a review answer was written but could not be indexed",
                extra={"unindexed": len(failure.report.unindexed_node_ids)},
            )
            return failure.report

    def _proposal_for(self, item: HitlQueueItemRecord) -> FrozenProposal:
        """Read back what was going to be written for this question."""
        payload = self._ops.hitl.get_proposal(item.audit_node_id)
        if payload is None:
            raise MissingProposal(item.id)
        return FrozenProposal.model_validate_json(payload)

    def _cards_for(
        self,
        items: list[HitlQueueItemRecord],
        *,
        now: datetime,
        stores: UserStores,
    ) -> list[QueueCard]:
        """
        Build a page of cards, reading the graph once for the whole page.

        An item with nothing recorded to carry out is shown too, with what it
        is about but no answers. Leaving it out is what makes the count and
        the list disagree, and a question nobody can see is a question nobody
        can withdraw either.
        """
        days = self._config.operational.hitl_auto_resolve_days
        by_id = {item.id: item for item in items}
        pairs: list[tuple[HitlQueueItemRecord, FrozenProposal]] = []
        stranded: dict[str, MissingProposal] = {}

        for item in items:
            try:
                pairs.append((item, self._proposal_for(item)))
            except MissingProposal as absent:
                # Shown rather than dropped. Leaving it out is what makes the
                # count and the list disagree, and a screen that says forty
                # are waiting above a list showing none helps nobody.
                stranded[item.id] = absent

        # One read for the whole page, covering both kinds of card. The
        # questions with nothing saved behind them still name a finding and
        # something it was weighed against, and those are exactly what makes
        # them mean anything to a person.
        proposals = [proposal for _, proposal in pairs]
        wanted = sorted(
            {*cards.wanted_node_ids(proposals), *cards.wanted_for_items(items)}
        )
        rows = cards.read_rows(wanted, graph=stores.graph)
        summaries = cards.read_episode_summaries(
            (item.episode_id or "" for item in items), graph=stores.graph
        )
        built = {
            item.id: cards.build_card(
                item,
                proposal,
                rows=rows,
                episode_summaries=summaries,
                now=now,
                auto_resolve_days=days,
            )
            for item, proposal in pairs
        }
        built.update(
            {
                item_id: cards.build_unanswerable_card(
                    by_id[item_id],
                    rows=rows,
                    episode_summaries=summaries,
                    now=now,
                    auto_resolve_days=days,
                    reason=str(absent),
                )
                for item_id, absent in stranded.items()
            }
        )

        # Back into the order the queue asked for, which the two groups lost
        # by being built apart.
        return [built[item.id] for item in items if item.id in built]

    def _owned(self, user_id: str, item_id: str) -> HitlQueueItemRecord:
        """
        Fetch an item, refusing anything that is not this person's.

        Someone else's item is reported as missing rather than forbidden. The
        second answer confirms that the item exists, which is a small leak
        about a store holding somebody's private history.
        """
        item = self._ops.hitl.get(item_id)
        if item is None or item.user_id != user_id:
            raise RecordNotFoundError(f"no review item with id {item_id!r}")
        return item

def _settled_as(choice: HitlResolutionChoice) -> HitlItemStatus:
    """
    Which settled state an answer leaves an item in.

    Everything a person chose is resolved; the one nobody chose is marked
    apart, so a queue read back later can tell what was decided from what
    merely ran out of time.
    """
    if choice is HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE:
        return HitlItemStatus.AUTO_RESOLVED
    return HitlItemStatus.RESOLVED


def _now() -> datetime:
    """The current moment, in UTC."""
    return datetime.now(timezone.utc)


__all__ = ["ReviewService", "MissingProposal"]
