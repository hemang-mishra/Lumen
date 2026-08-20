"""
The shapes an erasure is asked for in, and answers with.

Three models, and the split between them is the point.

A request says what to erase and proves it was meant. A plan says what would
happen and changes nothing, so somebody can look before agreeing to something
that cannot be taken back. A report says what did happen, and is what the
record of the erasure is written from.

Keeping the plan and the report apart rather than reusing one shape means a
preview can never be mistaken for a receipt.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lumen.operational.enums import ErasureInitiator, ErasureScope, ErasureStatus


class ErasureRequest(BaseModel):
    """
    An ask to forget something, and the proof that it was meant.

    Attributes:
        user_id: Whose history this is.
        scope: Everything, or one piece of writing.
        entry_id: Which piece of writing, when the scope is one of them.
        initiated_by: Who asked — the person, an administrator, or a policy.
        confirmation: The phrase this deployment requires before anything is
            erased. Checked by the service rather than here, because what
            counts as the right phrase is a setting and a model should not
            read settings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    scope: ErasureScope
    entry_id: str | None = None
    initiated_by: ErasureInitiator = ErasureInitiator.USER_REQUEST
    confirmation: str = ""

    @model_validator(mode="after")
    def _scope_and_entry_agree(self) -> "ErasureRequest":
        """
        An entry-sized erasure needs an entry, and a whole one must not name
        one.

        The second half matters as much as the first. A request that names an
        entry *and* asks for everything is somebody who meant one of the two,
        and guessing which would either erase far more than they wanted or
        far less.
        """
        if self.scope is ErasureScope.ENTRY and not self.entry_id:
            raise ValueError("erasing one entry means saying which entry")
        if self.scope is ErasureScope.ALL and self.entry_id:
            raise ValueError(
                "an erasure of everything cannot also name a single entry"
            )
        return self


class ErasurePlan(BaseModel):
    """
    What an erasure would do, worked out without doing any of it.

    Attributes:
        scope: What was asked about.
        entry_id: Which piece of writing, when that is what was asked about.
        records_by_kind: How many records of each kind would be rewritten.
        total_records: All of them together.
        vectors: How many index entries would go.
        conversations: How many conversations in the working database would
            be blanked.
        not_reached: Things a person might reasonably expect to be covered
            and which will not be, said plainly rather than left to be
            discovered afterwards.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: ErasureScope
    entry_id: str | None = None
    records_by_kind: dict[str, int] = Field(default_factory=dict)
    total_records: int = Field(default=0, ge=0)
    vectors: int = Field(default=0, ge=0)
    conversations: int = Field(default=0, ge=0)
    not_reached: tuple[str, ...] = ()


class ErasureReport(BaseModel):
    """
    What an erasure actually did.

    Attributes:
        audit_id: The record written to prove it happened.
        scope: What was asked for.
        entry_id: Which piece of writing, where that was the ask.
        status: Whether it finished.
        records_anonymized: How many records had their words replaced.
        vectors_deleted: How many index entries were actually removed —
            counted, not assumed, because this ends up in a compliance
            record.
        operational_rows_cleared: How many rows of the working database were
            blanked or dropped.
        entry_ids_affected: Which pieces of writing were covered.
        failures: What went wrong, in short words. A failed erasure is not
            rolled back — less content than before is the direction that was
            asked for, and putting the words back would undo exactly what
            somebody requested.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: str = Field(min_length=1)
    scope: ErasureScope
    entry_id: str | None = None
    status: ErasureStatus = ErasureStatus.IN_PROGRESS
    records_anonymized: int = Field(default=0, ge=0)
    vectors_deleted: int = Field(default=0, ge=0)
    operational_rows_cleared: int = Field(default=0, ge=0)
    entry_ids_affected: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()

    @property
    def finished(self) -> bool:
        """Whether everything asked for was carried out."""
        return self.status is ErasureStatus.COMPLETE


class ErasureRefused(ValueError):
    """
    The request was not carried out, and nothing was touched.

    A separate kind of error from anything raised while erasing. This one
    means the ask itself was wrong — an unknown entry, a missing confirmation
    — and the caller can fix it and try again. Once erasing has started there
    is no trying again.
    """


__all__ = [
    "ErasureRequest",
    "ErasurePlan",
    "ErasureReport",
    "ErasureRefused",
]
