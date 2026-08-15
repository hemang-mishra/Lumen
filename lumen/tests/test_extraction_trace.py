"""
Tests that an extraction run can be followed afterwards, and that
following it does not mean reading someone's journal.

Two things are being protected here and they pull against each other. A
run has to leave enough behind to work out what happened when something
goes wrong. It also must not leave the writing itself behind, because a
log file is not encrypted, is trivial to copy, and would slowly become a
second copy of the most private thing the system holds.

The answer is that logs carry counts, outcomes and identifiers, never
content — and this stage has more of the content passing through it than
any other, since every quote it handles is the person's own words.
"""

from __future__ import annotations

import json

from lumen.pipeline import extract
from lumen.schemas.enums import EntryClass

# A sentence distinctive enough that finding it anywhere in the logs proves
# the entry leaked.
PRIVATE = "the thing about my father I have never said out loud to anyone"
PRIVATE_ENTRY = (
    f"I finally wrote down {PRIVATE}, and afterwards I felt lighter than I have in years."
)


def reply_quoting_the_entry() -> str:
    """A reply that repeats the private sentence back as evidence."""
    return json.dumps(
        {
            "observations": [
                {
                    "type": "ACCEPTANCE_ACKNOWLEDGEMENT",
                    "content": "Wrote down something long withheld",
                    "raw_evidence": [PRIVATE],
                }
            ],
            "events": [],
            "causal_mechanisms": [],
        }
    )


def everything(records) -> str:
    """Flatten every captured log line into one searchable string."""
    return json.dumps(records)


class TestTheTraceReaches:
    def test_the_result_carries_the_running_trace(
        self, make_extraction_input, extraction_providers, bound_trace
    ):
        light, thinking = extraction_providers({"reflection": reply_quoting_the_entry()})

        result = extract(
            make_extraction_input(PRIVATE_ENTRY), lightweight=light, thinking=thinking
        )

        assert result.trace_id == bound_trace

    def test_every_log_line_carries_the_trace(
        self, make_extraction_input, extraction_providers, bound_trace, captured_logs
    ):
        light, thinking = extraction_providers({"reflection": reply_quoting_the_entry()})

        extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert captured_logs
        assert all(line["trace_id"] == bound_trace for line in captured_logs)

    def test_a_failed_reading_is_still_traceable(
        self, make_extraction_input, extraction_providers, bound_trace, captured_logs
    ):
        light, thinking = extraction_providers({"reflection": "not json"})

        extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert all(line["trace_id"] == bound_trace for line in captured_logs)


class TestTheWritingStaysOut:
    def test_a_successful_run_never_logs_the_entry(
        self, make_extraction_input, extraction_providers, captured_logs
    ):
        light, thinking = extraction_providers({"reflection": reply_quoting_the_entry()})

        extract(
            make_extraction_input(PRIVATE_ENTRY), lightweight=light, thinking=thinking
        )

        assert PRIVATE not in everything(captured_logs)

    def test_a_failed_run_never_logs_the_entry(
        self, make_extraction_input, extraction_providers, captured_logs
    ):
        light, thinking = extraction_providers({"reflection": "not json"})

        extract(
            make_extraction_input(PRIVATE_ENTRY), lightweight=light, thinking=thinking
        )

        assert PRIVATE not in everything(captured_logs)

    def test_a_thin_run_never_logs_the_entry(
        self, make_extraction_input, extraction_providers, captured_logs
    ):
        light, thinking = extraction_providers(
            {"raw_capture": json.dumps({"context": "Wrote something down"})}
        )

        extract(
            make_extraction_input(PRIVATE_ENTRY, entry_class=EntryClass.RAW_CAPTURE),
            lightweight=light,
            thinking=thinking,
        )

        assert PRIVATE not in everything(captured_logs)

    def test_a_dropped_item_is_reported_without_its_content(
        self, make_extraction_input, extraction_providers, captured_logs
    ):
        # The most tempting place to log content is the note explaining why
        # something was thrown away, which is exactly where it must not be.
        light, thinking = extraction_providers(
            {
                "reflection": json.dumps(
                    {
                        "observations": [
                            {
                                "type": "INVENTED_TYPE",
                                "content": PRIVATE,
                                "raw_evidence": [PRIVATE],
                            }
                        ]
                    }
                )
            }
        )

        extract(
            make_extraction_input(PRIVATE_ENTRY), lightweight=light, thinking=thinking
        )

        logged = everything(captured_logs)
        assert PRIVATE not in logged
        assert "UNKNOWN_TYPE" in logged


class TestWhatTheLogDoesSay:
    def test_the_closing_line_counts_what_was_produced(
        self, make_extraction_input, extraction_providers, captured_logs
    ):
        light, thinking = extraction_providers({"reflection": reply_quoting_the_entry()})

        extract(make_extraction_input(), lightweight=light, thinking=thinking)

        line = next(
            entry for entry in captured_logs if entry["msg"] == "extraction complete"
        )
        assert line["observations"] == 1
        assert line["anchored"] is True
        assert line["read_failed"] is False
        assert line["duration_ms"] >= 0

    def test_the_closing_line_names_the_episode(
        self, make_extraction_input, extraction_providers, captured_logs
    ):
        light, thinking = extraction_providers({"reflection": reply_quoting_the_entry()})
        payload = make_extraction_input()

        extract(payload, lightweight=light, thinking=thinking)

        line = next(
            entry for entry in captured_logs if entry["msg"] == "extraction complete"
        )
        assert line["episode_id"] == payload.episode.episode_id
        assert line["entry_id"] == payload.entry_id

    def test_a_failed_reading_is_reported_as_failed(
        self, make_extraction_input, extraction_providers, captured_logs
    ):
        light, thinking = extraction_providers({"reflection": "not json"})

        extract(make_extraction_input(), lightweight=light, thinking=thinking)

        line = next(
            entry for entry in captured_logs if entry["msg"] == "extraction complete"
        )
        assert line["read_failed"] is True
        assert line["observations"] == 0

    def test_unquotable_findings_are_counted(
        self, make_extraction_input, extraction_providers, captured_logs
    ):
        # This count is the only visible sign of the failure this stage is
        # most prone to: a well-formed finding that nobody actually said.
        light, thinking = extraction_providers(
            {
                "reflection": json.dumps(
                    {
                        "observations": [
                            {
                                "type": "BELIEF",
                                "content": "He believes effort is always punished",
                                "raw_evidence": ["words that appear nowhere in the entry"],
                            }
                        ]
                    }
                )
            }
        )

        extract(make_extraction_input(), lightweight=light, thinking=thinking)

        line = next(
            entry for entry in captured_logs if entry["msg"] == "extraction complete"
        )
        assert line["ungrounded"] == 1
