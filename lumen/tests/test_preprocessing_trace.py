"""
Tests that a preprocessing run can be followed afterwards, and that
following it does not mean reading someone's journal.

Two things are being protected here and they pull in opposite directions.
A run has to leave enough behind to work out what happened when something
goes wrong. It also must not leave the entry itself behind, because a log
file is not encrypted, is easy to copy, and would slowly become a second
copy of the most private thing the system holds.

The answer is that logs carry counts, outcomes and identifiers, never
content.
"""

from __future__ import annotations

import json

from lumen.pipeline import preprocess
from lumen.schemas.enums import SourceModality

LONG_ENOUGH = " ".join(f"word{index}" for index in range(40))

# A sentence distinctive enough that finding it anywhere in the logs proves
# the entry leaked.
PRIVATE = "the thing about my father I have never said out loud to anyone"
PRIVATE_ENTRY = f"{PRIVATE}. {LONG_ENOUGH}"


def working_script(text: str) -> dict:
    return {
        "normalize_text": json.dumps(
            {"cleaned_text": text, "detected_languages": ["en"], "translated": False}
        ),
        "normalize_voice": json.dumps(
            {"cleaned_text": text, "detected_languages": ["en"], "translated": False}
        ),
        "structure": json.dumps(
            {
                "episodes": [{"episode_summary": "a topic", "text": text}],
                "coreference": {"resolved_entities": [], "ambiguous_refs": []},
            }
        ),
        "triage": json.dumps(
            {"scores": [{"episode_index": 1, "coherence_score": 0.9, "reason": "clear"}]}
        ),
    }


def logs_from(records) -> str:
    """Flatten every captured log line into one searchable string."""
    return json.dumps(records)


class TestTraceIdReaches:
    def test_the_result_carries_the_running_trace(
        self, make_event, scripted_providers, bound_trace
    ):
        light, thinking = scripted_providers(working_script(LONG_ENOUGH))
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.trace_id == bound_trace

    def test_every_log_line_carries_the_running_trace(
        self, make_event, scripted_providers, bound_trace, captured_logs
    ):
        light, thinking = scripted_providers(working_script(LONG_ENOUGH))
        preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert captured_logs
        assert all(line["trace_id"] == bound_trace for line in captured_logs)

    def test_fallback_warnings_carry_it_too(
        self, make_event, scripted_providers, bound_trace, captured_logs
    ):
        light, thinking = scripted_providers({})
        preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        warnings = [line for line in captured_logs if line["level"] == "WARNING"]
        assert warnings
        assert all(line["trace_id"] == bound_trace for line in warnings)


class TestClosingLogLine:
    def _closing_line(self, records):
        return next(
            line for line in records if line["msg"] == "preprocessing complete"
        )

    def test_it_reports_what_happened_without_quoting_the_entry(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers(working_script(LONG_ENOUGH))
        preprocess(
            make_event([("USER", LONG_ENOUGH)], session_id="sess_log"),
            lightweight=light,
            thinking=thinking,
        )

        line = self._closing_line(captured_logs)
        assert line["session_id"] == "sess_log"
        assert line["decision"] == "REFLECTION"
        assert line["episode_count"] == 1
        assert line["clean_word_count"] == 40
        assert line["duration_ms"] >= 0

    def test_it_names_the_steps_that_fell_back(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers({})
        preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        line = self._closing_line(captured_logs)
        assert set(line["fallbacks"]) == {"normalize", "structure", "triage"}

    def test_a_clean_run_names_no_fallbacks(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers(working_script(LONG_ENOUGH))
        preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert self._closing_line(captured_logs)["fallbacks"] == []

    def test_translation_is_accounted_for(
        self, make_event, scripted_providers, captured_logs
    ):
        script = working_script(LONG_ENOUGH)
        script["normalize_text"] = json.dumps(
            {
                "cleaned_text": LONG_ENOUGH,
                "detected_languages": ["en", "hi"],
                "translated": True,
            }
        )
        light, thinking = scripted_providers(script)
        preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        line = self._closing_line(captured_logs)
        assert line["translated"] is True
        assert line["detected_languages"] == ["en", "hi"]

    def test_removed_hesitations_are_counted(
        self, make_event, scripted_providers, captured_logs
    ):
        raw = "um " + LONG_ENOUGH + " uh"
        light, thinking = scripted_providers(working_script(LONG_ENOUGH))
        preprocess(
            make_event([("USER", raw)], source_modality=SourceModality.VOICE_NOTE),
            lightweight=light,
            thinking=thinking,
        )

        assert self._closing_line(captured_logs)["fillers_removed"] == 2

    def test_a_discarded_session_says_why(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers(
            {"normalize_text": json.dumps({"cleaned_text": ""})}
        )
        preprocess(make_event([]), lightweight=light, thinking=thinking)

        line = self._closing_line(captured_logs)
        assert line["decision"] == "DISCARD"
        assert "nothing extractable" in line["reason"]

    def test_a_short_entry_says_why(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers(
            {
                "normalize_text": json.dumps({"cleaned_text": "just a few words"}),
                "reflection": json.dumps({"reflection_prompts": []}),
            }
        )
        preprocess(
            make_event([("USER", "just a few words")]),
            lightweight=light,
            thinking=thinking,
        )

        assert self._closing_line(captured_logs)["reason"] == "too short to segment"


class TestPrivacy:
    def test_a_successful_run_never_writes_the_entry_to_the_log(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers(working_script(PRIVATE_ENTRY))
        preprocess(
            make_event([("USER", PRIVATE_ENTRY)]), lightweight=light, thinking=thinking
        )

        assert PRIVATE not in logs_from(captured_logs)

    def test_a_failing_run_never_writes_it_either(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers({})
        preprocess(
            make_event([("USER", PRIVATE_ENTRY)]), lightweight=light, thinking=thinking
        )

        assert PRIVATE not in logs_from(captured_logs)

    def test_a_discarded_entry_is_not_quoted_on_its_way_out(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers(
            {"normalize_text": json.dumps({"cleaned_text": ""})}
        )
        preprocess(
            make_event([("USER", PRIVATE)]), lightweight=light, thinking=thinking
        )

        assert PRIVATE not in logs_from(captured_logs)

    def test_a_short_entry_is_not_quoted(
        self, make_event, scripted_providers, captured_logs
    ):
        light, thinking = scripted_providers(
            {
                "normalize_text": json.dumps({"cleaned_text": PRIVATE}),
                "reflection": json.dumps({"reflection_prompts": []}),
            }
        )
        preprocess(make_event([("USER", PRIVATE)]), lightweight=light, thinking=thinking)

        assert PRIVATE not in logs_from(captured_logs)

    def test_a_multi_date_warning_names_dates_not_content(
        self, make_event, scripted_providers, captured_logs
    ):
        from datetime import date

        light, thinking = scripted_providers(working_script(PRIVATE_ENTRY))
        preprocess(
            make_event(
                [("USER", PRIVATE_ENTRY), ("USER", "second")],
                message_dates=[date(2026, 6, 10), date(2026, 6, 11)],
            ),
            lightweight=light,
            thinking=thinking,
        )

        assert PRIVATE not in logs_from(captured_logs)
        assert "2026-06-10" in logs_from(captured_logs)
