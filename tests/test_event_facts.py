"""P2-1 — extracting event facts into options the coordinator confirms.

The core rule, same as everywhere else in this tool: scraped data is *proposed*,
never applied. Each extracted fact becomes an option carrying its source
attribution as reasoning, and the human confirms or edits it.

A scraper is confidently wrong more often than it is silent, which is exactly
why nothing here writes to the event until someone says yes.
"""
from __future__ import annotations

import pytest

from app.features.event_facts import (
    FACT_FIELDS,
    ExtractedFact,
    build_fact_options,
    extract_facts,
    strip_html,
)

PAGE = """
<html><head><title>Fleet Week 2026 | Rotterdam Ahoy</title>
<script>var tracking = {"junk": true};</script>
<style>.hidden{display:none}</style></head>
<body>
  <h1>Saronic Fleet Week 2026</h1>
  <p>14&ndash;16 March 2026 &middot; Rotterdam Ahoy, Rotterdam, Netherlands</p>
  <p>Expected attendance: 4,200 defense and maritime professionals.</p>
</body></html>
"""


class _Claude:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def complete(self, *, system, prompt, **kw):
        self.calls.append({"system": system, "prompt": prompt})
        return self.response


class _Failing:
    def __init__(self, exc):
        self.exc = exc

    def complete(self, **kw):
        raise self.exc


GOOD_JSON = """{
  "event_name": "Saronic Fleet Week 2026",
  "start_date": "2026-03-14",
  "end_date": "2026-03-16",
  "city": "Rotterdam",
  "country": "Netherlands",
  "venue": "Rotterdam Ahoy",
  "expected_attendance": 4200
}"""


class TestStripHtml:
    def test_removes_tags_scripts_and_styles(self):
        text = strip_html(PAGE)
        assert "<h1>" not in text
        assert "tracking" not in text
        assert "display:none" not in text

    def test_keeps_the_visible_copy(self):
        text = strip_html(PAGE)
        assert "Saronic Fleet Week 2026" in text
        assert "Rotterdam Ahoy" in text

    def test_decodes_entities(self):
        text = strip_html(PAGE)
        assert "–" in text or "-" in text
        assert "&ndash;" not in text

    def test_collapses_whitespace(self):
        assert "\n\n\n" not in strip_html(PAGE)

    def test_empty_input_is_safe(self):
        assert strip_html("") == ""


class TestExtractFacts:
    def test_parses_every_field_from_a_clean_response(self):
        facts = extract_facts(_Claude(GOOD_JSON), PAGE, source_url="https://x.test/e")
        by_field = {f.field: f for f in facts}
        assert by_field["city"].value == "Rotterdam"
        assert by_field["venue"].value == "Rotterdam Ahoy"
        assert by_field["expected_attendance"].value == "4200"

    def test_every_fact_carries_its_source_url(self):
        facts = extract_facts(_Claude(GOOD_JSON), PAGE, source_url="https://x.test/e")
        assert all(f.source_url == "https://x.test/e" for f in facts)

    def test_the_page_text_reaches_the_prompt_but_not_the_markup(self):
        client = _Claude(GOOD_JSON)
        extract_facts(client, PAGE, source_url="https://x.test/e")
        prompt = client.calls[0]["prompt"]
        assert "Rotterdam Ahoy" in prompt
        assert "<script>" not in prompt

    def test_json_wrapped_in_prose_or_fences_is_still_parsed(self):
        wrapped = f"Here is what I found:\n```json\n{GOOD_JSON}\n```\nHope that helps."
        facts = extract_facts(_Claude(wrapped), PAGE, source_url="https://x.test/e")
        assert any(f.field == "city" and f.value == "Rotterdam" for f in facts)

    def test_missing_fields_are_simply_absent(self):
        facts = extract_facts(_Claude('{"city": "Austin"}'), PAGE,
                              source_url="https://x.test/e")
        assert [f.field for f in facts] == ["city"]

    def test_null_and_empty_values_are_dropped(self):
        facts = extract_facts(
            _Claude('{"city": "Austin", "venue": null, "country": "  "}'),
            PAGE, source_url="https://x.test/e")
        assert [f.field for f in facts] == ["city"]

    def test_unknown_fields_are_ignored(self):
        """A model inventing a field must not smuggle it into the event."""
        facts = extract_facts(
            _Claude('{"city": "Austin", "ticket_price": "$500"}'),
            PAGE, source_url="https://x.test/e")
        assert [f.field for f in facts] == ["city"]
        assert all(f.field in FACT_FIELDS for f in facts)

    def test_unparseable_response_yields_nothing_rather_than_raising(self):
        assert extract_facts(_Claude("I could not find any event details."),
                             PAGE, source_url="https://x.test/e") == []

    def test_no_client_yields_nothing(self):
        assert extract_facts(None, PAGE, source_url="https://x.test/e") == []

    def test_claude_failure_degrades_to_nothing(self):
        from app.claude.errors import BudgetExceededError

        facts = extract_facts(_Failing(BudgetExceededError(spent=1.0, limit=1.0)),
                              PAGE, source_url="https://x.test/e")
        assert facts == []

    def test_mock_scaffolding_is_never_treated_as_facts(self):
        mocky = _Claude("[MOCK CLAUDE] Offline completion. system='...' prompt='...'")
        assert extract_facts(mocky, PAGE, source_url="https://x.test/e") == []


class TestBuildFactOptions:
    def test_each_fact_becomes_a_confirmable_option(self):
        facts = extract_facts(_Claude(GOOD_JSON), PAGE, source_url="https://x.test/e")
        opts = build_fact_options(facts)
        assert len(opts) == len(facts)

    def test_reasoning_attributes_the_source(self):
        facts = extract_facts(_Claude(GOOD_JSON), PAGE,
                              source_url="https://ahoy.test/fleet-week")
        city = next(o for o in build_fact_options(facts) if o.key == "city")
        assert "ahoy.test" in city.reasoning
        assert "extracted" in city.reasoning.lower() or "from" in city.reasoning.lower()

    def test_options_are_editable_not_just_confirmable(self):
        facts = extract_facts(_Claude(GOOD_JSON), PAGE, source_url="https://x.test/e")
        assert all(o.data.get("requires_value") for o in build_fact_options(facts))

    def test_the_extracted_value_is_the_prefilled_default(self):
        facts = extract_facts(_Claude(GOOD_JSON), PAGE, source_url="https://x.test/e")
        city = next(o for o in build_fact_options(facts) if o.key == "city")
        assert city.data["suggested"] == "Rotterdam"

    def test_no_facts_yields_no_options(self):
        assert build_fact_options([]) == []

    def test_a_fact_is_never_silently_applied(self):
        """Nothing here writes to the event — options only."""
        fact = ExtractedFact(field="city", value="Rotterdam",
                             source_url="https://x.test/e")
        opt = build_fact_options([fact])[0]
        assert opt.data["suggested"] == "Rotterdam"
        assert "confirm" in opt.reasoning.lower() or "check" in opt.reasoning.lower()
