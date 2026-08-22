"""Claude-generated slide copy — the one Claude-driven surface in the app.

Contract this pins:

* Mock mode must NEVER leak "[MOCK CLAUDE]" text into a deck. If the model is
  unavailable, we fall back to deterministic copy rather than showing scaffolding
  to a room full of people.
* A Claude failure (budget, rate limit, bad key) degrades the copy, never the
  deck. Same failure ordering as stock imagery.
* The prompt is built from the coordinator's actual decisions, so the copy cannot
  describe an event that was not planned.
"""
from __future__ import annotations

import pytest

from app.claude.errors import BudgetExceededError, ClaudeError, RateLimitError
from app.features.slide_copy import (
    FALLBACK_SUBHEAD,
    SlideCopy,
    build_copy_prompt,
    generate_title_copy,
)


class _Client:
    def __init__(self, response="Fleet Week\nAutonomous surface vessels, on the water."):
        self.response = response
        self.calls = []

    def complete(self, *, system, prompt, **kw):
        self.calls.append({"system": system, "prompt": prompt})
        return self.response


class _Failing:
    def __init__(self, exc):
        self.exc = exc
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        raise self.exc


class _Mocky:
    """Mirrors MockClaudeClient's actual output shape."""

    def complete(self, **kwargs):
        return "[MOCK CLAUDE] Offline completion. system='...' prompt='...'"


EVENT = {"name": "Saronic Fleet Week", "city": "Austin",
         "event_type": "convention", "audience": 3600,
         "venue": "Austin Convention Center"}


class TestPromptConstruction:
    def test_prompt_carries_the_real_decisions(self):
        p = build_copy_prompt(**EVENT)
        for token in ("Saronic Fleet Week", "Austin", "convention",
                      "3,600", "Austin Convention Center"):
            assert token in p

    def test_prompt_states_the_brand_voice_constraint(self):
        p = build_copy_prompt(**EVENT)
        assert "superlative" in p.lower() or "understated" in p.lower()

    def test_prompt_survives_missing_optional_decisions(self):
        p = build_copy_prompt(name="E", city=None, event_type=None,
                              audience=None, venue=None)
        assert "E" in p


class TestGenerateTitleCopy:
    def test_parses_headline_and_subhead(self):
        copy = generate_title_copy(_Client(), **EVENT)
        assert copy.headline == "Fleet Week"
        assert copy.subhead == "Autonomous surface vessels, on the water."
        assert copy.source == "claude"

    def test_headline_is_capped_at_six_words(self):
        long = "One Two Three Four Five Six Seven Eight\nSub line here."
        copy = generate_title_copy(_Client(long), **EVENT)
        assert len(copy.headline.split()) <= 6

    def test_single_line_response_still_yields_a_subhead(self):
        copy = generate_title_copy(_Client("Just A Headline"), **EVENT)
        assert copy.headline == "Just A Headline"
        assert copy.subhead == FALLBACK_SUBHEAD

    def test_blank_response_falls_back_entirely(self):
        copy = generate_title_copy(_Client("   \n  "), **EVENT)
        assert copy.source == "fallback"
        assert copy.headline == "Saronic Fleet Week"

    def test_markdown_decoration_is_stripped(self):
        copy = generate_title_copy(_Client("## **Fleet Week**\n_On the water._"), **EVENT)
        assert "*" not in copy.headline and "#" not in copy.headline
        assert "_" not in copy.subhead


class TestMockOutputNeverReachesADeck:
    def test_mock_scaffolding_is_rejected_as_copy(self):
        """A deck must never display '[MOCK CLAUDE]' to an audience."""
        copy = generate_title_copy(_Mocky(), **EVENT)
        assert "MOCK" not in copy.headline
        assert "MOCK" not in copy.subhead
        assert copy.source == "fallback"

    def test_no_client_at_all_uses_deterministic_copy(self):
        copy = generate_title_copy(None, **EVENT)
        assert copy.source == "fallback"
        assert copy.headline == "Saronic Fleet Week"


class TestFailuresDegradeCopyNotTheDeck:
    @pytest.mark.parametrize("exc", [
        BudgetExceededError(spent=250.0, limit=250.0),
        RateLimitError(retry_after=30),
        ClaudeError("upstream exploded"),
    ])
    def test_claude_failure_returns_fallback_copy(self, exc):
        client = _Failing(exc)
        copy = generate_title_copy(client, **EVENT)
        assert copy.source == "fallback"
        assert copy.headline == "Saronic Fleet Week"
        assert client.calls, "the failure must come from a real attempt"

    def test_unexpected_exception_also_degrades_safely(self):
        copy = generate_title_copy(_Failing(RuntimeError("boom")), **EVENT)
        assert copy.source == "fallback"

    def test_fallback_copy_is_still_presentable(self):
        copy = generate_title_copy(None, **EVENT)
        assert copy.subhead and copy.headline
        assert "Austin" in copy.subhead or "3,600" in copy.subhead


class TestSlideCopyValue:
    def test_source_is_reported_for_honest_attribution(self):
        assert SlideCopy("h", "s", "claude").source == "claude"
        assert SlideCopy("h", "s", "fallback").source == "fallback"
