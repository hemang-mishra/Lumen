"""
The review queue: the questions Lumen could not answer on its own.

The pipeline knows when it cannot decide something — two readings too close
to separate, or one reading it is not confident enough to act on. It writes
a note saying so and stops, and the change it was about to make is held
back. This package is the other half of that: showing those questions,
taking somebody's answer, and making the answer land in the graph.

The pieces, in the order a question moves through them:

    capacity     how many questions to ask at once, and what to do past that
    cards        turn a saved question into something answerable in seconds
    resolve      turn an answer into the writing that was held back
    housekeeping the two things that happen on a clock rather than on a tap
    service      the narrow surface the web layer holds

What each question was about to write is kept by the reconciliation stage
at the moment it gives up, not here — the thing being saved is what that
stage was about to do, and this package only ever reads it back.

Only `service` touches anything. Everything else is a function you can call
with made-up data and check by hand.
"""

from lumen.review.contracts import (
    CandidatePreview,
    CardOption,
    ChoiceNotOffered,
    QueueCard,
    QueueCounts,
    QueueView,
    ResolutionChoice,
    ResolutionOutcome,
    ResolutionPlan,
    ReviewError,
    StaleProposal,
    SweepReport,
)
from lumen.review.service import MissingProposal, ReviewService

__all__ = [
    "CandidatePreview",
    "CardOption",
    "ChoiceNotOffered",
    "MissingProposal",
    "QueueCard",
    "QueueCounts",
    "QueueView",
    "ResolutionChoice",
    "ResolutionOutcome",
    "ResolutionPlan",
    "ReviewError",
    "ReviewService",
    "StaleProposal",
    "SweepReport",
]
