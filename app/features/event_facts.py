"""P2-1 — turn a fetched event page into facts the coordinator confirms.

Claude is the right tool for this specific job: pulling structured values out of
arbitrary marketing HTML is a language problem, not a parsing one, and every
conference site is shaped differently.

It is also the job where a model is most likely to be *confidently wrong* — a
plausible date on the wrong year, a venue that is actually a sponsor. So nothing
extracted here is ever applied. Each fact becomes an option carrying its source,
prefilled and editable, and the coordinator confirms it. Same contract as every
other slate in the tool: options plus reasoning, the human decides.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import List, Optional

from app.db.models import DecisionOption

#: The only fields we accept from a model response. Anything else is ignored —
#: an invented field must never reach the event record.
FACT_FIELDS: tuple[str, ...] = (
    "event_name",
    "start_date",
    "end_date",
    "city",
    "country",
    "venue",
    "expected_attendance",
)

FIELD_LABELS = {
    "event_name": "Event name",
    "start_date": "Start date",
    "end_date": "End date",
    "city": "City",
    "country": "Country",
    "venue": "Venue",
    "expected_attendance": "Expected attendance",
}

SYSTEM = (
    "You extract factual event details from web pages for a human event "
    "coordinator who will check your work. Report only what the page actually "
    "states. If a field is not present, omit it — never guess, never infer a "
    "year that is not written down. Respond with a single JSON object and "
    "nothing else."
)

#: Mock-client scaffolding must never be mistaken for extracted data.
_MOCK_MARKERS = ("[MOCK CLAUDE]", "MOCK CLAUDE", "Offline completion")

#: Page text sent to the model. Enough for a long agenda page, bounded so a
#: hostile page cannot inflate our token spend.
MAX_PROMPT_CHARS = 12000

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>",
                           re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


@dataclass
class ExtractedFact:
    field: str
    value: str
    source_url: str


def strip_html(markup: str) -> str:
    """Reduce markup to the visible text a model should reason over.

    Dropping script/style first matters for cost as much as quality: inline
    tracking blobs are often larger than the page copy.
    """
    if not markup:
        return ""
    text = _SCRIPT_STYLE.sub(" ", markup)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()


def _parse_json_object(raw: str) -> Optional[dict]:
    """Pull a JSON object out of a response that may be wrapped in prose."""
    if not raw or not raw.strip():
        return None
    if any(marker in raw for marker in _MOCK_MARKERS):
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def build_extraction_prompt(page_text: str) -> str:
    return (
        "Extract the event details stated on this page.\n\n"
        "Return a JSON object using only these keys, omitting any that the page "
        f"does not state: {', '.join(FACT_FIELDS)}.\n"
        "Dates must be ISO format (YYYY-MM-DD). expected_attendance must be a "
        "plain integer.\n\n"
        "--- PAGE TEXT ---\n"
        f"{page_text[:MAX_PROMPT_CHARS]}"
    )


def extract_facts(client, markup: str, source_url: str) -> List[ExtractedFact]:
    """Ask Claude for the facts on the page. Never raises; returns [] on failure.

    A failed extraction is a detour to manual entry, not an error — so every
    failure mode (no client, budget, rate limit, unparseable response) lands in
    the same place.
    """
    if client is None:
        return []
    page_text = strip_html(markup)
    if not page_text:
        return []

    try:
        raw = client.complete(
            system=SYSTEM,
            prompt=build_extraction_prompt(page_text),
            # Reasoning models spend part of this budget thinking before any
            # text is emitted; too small a budget returns nothing at all.
            max_tokens=2000,
            temperature=0.0,
        )
    except Exception:
        return []

    payload = _parse_json_object(raw)
    if not payload:
        return []

    facts: List[ExtractedFact] = []
    for field in FACT_FIELDS:          # iterate the allowlist, not the response
        if field not in payload:
            continue
        value = payload[field]
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        facts.append(ExtractedFact(field=field, value=text, source_url=source_url))
    return facts


def build_fact_options(facts: List[ExtractedFact]) -> List[DecisionOption]:
    """Each extracted fact as a confirmable, editable option."""
    options: List[DecisionOption] = []
    for fact in facts:
        label = FIELD_LABELS.get(fact.field, fact.field.replace("_", " ").capitalize())
        host = fact.source_url.split("//")[-1].split("/")[0]
        options.append(
            DecisionOption(
                key=fact.field,
                label=label,
                reasoning=(
                    f"Extracted from {host}: \u201c{fact.value}\u201d. "
                    "Confirm or correct it — scraped details are proposals, not "
                    "facts, and a wrong date here propagates through the plan."
                ),
                data={
                    "requires_value": True,
                    "suggested": fact.value,
                    "source_url": fact.source_url,
                    "field": fact.field,
                },
            )
        )
    return options
