"""Claude-generated title-slide copy.

This is the one place a language model is wired into the running app, and it is
deliberately the narrowest useful surface: writing a headline is a genuine
language task, whereas venue fit and audience bracketing are arithmetic and
belong in deterministic code where they can be tested exactly.

Failure ordering mirrors stock imagery: a Claude problem costs you *better copy*,
never the deck. Everything degrades to deterministic text built from the
coordinator's own decisions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

SYSTEM = (
    "You write presentation copy for Saronic, a defense-technology company "
    "building autonomous surface vessels. The brand voice is spare, technical "
    "and understated. Never use marketing superlatives, exclamation marks, or "
    "words like 'revolutionary', 'cutting-edge', or 'game-changing'."
)

FALLBACK_SUBHEAD = "An event brief from the Saronic planning tool."

#: Markers that identify scaffolding from the offline mock client. If any appear
#: we treat the response as unusable — showing '[MOCK CLAUDE]' on a title slide
#: in front of an audience is worse than plain deterministic copy.
_MOCK_MARKERS = ("[MOCK CLAUDE]", "MOCK CLAUDE", "Offline completion")

_MAX_HEADLINE_WORDS = 6


@dataclass
class SlideCopy:
    headline: str
    subhead: str
    #: "claude" or "fallback" — surfaced so the UI and README can attribute
    #: honestly rather than implying every deck is model-written.
    source: str = "fallback"


def build_copy_prompt(name: str, city: Optional[str] = None,
                      event_type: Optional[str] = None,
                      audience: Optional[int] = None,
                      venue: Optional[str] = None) -> str:
    """Build the prompt from the coordinator's actual decisions.

    Grounding the prompt in the decision log is what stops the copy describing
    an event nobody planned.
    """
    facts = [f"Event name: {name}"]
    if city:
        facts.append(f"City: {city}")
    if event_type:
        facts.append(f"Format: {event_type}")
    if audience:
        facts.append(f"Expected attendance: {audience:,}")
    if venue:
        facts.append(f"Venue: {venue}")

    return (
        "Write title-slide copy for the following event.\n\n"
        + "\n".join(facts)
        + "\n\nReturn exactly two lines and nothing else:\n"
        "Line 1: a headline of at most six words.\n"
        "Line 2: a single-sentence subhead.\n"
        "No markdown, no labels, no superlatives — keep it understated."
    )


def _clean(line: str) -> str:
    line = re.sub(r"^\s*(headline|subhead|line \d)\s*[:\-]\s*", "", line,
                  flags=re.IGNORECASE)
    line = re.sub(r"[*_`#]+", "", line)          # strip markdown decoration
    return line.strip().strip('"').strip()


def _fallback(name: str, city: Optional[str], audience: Optional[int]) -> SlideCopy:
    bits = [b for b in (city, f"{audience:,} attendees" if audience else None) if b]
    subhead = " · ".join(bits) if bits else FALLBACK_SUBHEAD
    return SlideCopy(headline=name, subhead=subhead, source="fallback")


def generate_title_copy(client, name: str, city: Optional[str] = None,
                        event_type: Optional[str] = None,
                        audience: Optional[int] = None,
                        venue: Optional[str] = None,
                        event_id=None) -> SlideCopy:
    """Ask Claude for title copy, degrading to deterministic text on any problem.

    ``client`` may be None (no Claude configured), the mock client, or the real
    one — all three are safe.
    """
    fallback = _fallback(name, city, audience)
    if client is None:
        return fallback

    try:
        raw = client.complete(
            system=SYSTEM,
            prompt=build_copy_prompt(name, city, event_type, audience, venue),
            # Reasoning models consume part of this budget on thinking blocks
            # before emitting text; too small a budget yields an empty response.
            max_tokens=2000,
            temperature=0.6,
            event_id=event_id,
            surface="slide_copy",
        )
    except Exception:
        # Budget, rate limit, bad key, or anything unforeseen: the deck still
        # renders. A missing headline must never be a missing slide.
        return fallback

    if not raw or not raw.strip():
        return fallback
    if any(marker in raw for marker in _MOCK_MARKERS):
        return fallback

    lines = [_clean(l) for l in raw.strip().splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        return fallback

    headline = " ".join(lines[0].split()[:_MAX_HEADLINE_WORDS])
    subhead = lines[1] if len(lines) > 1 else FALLBACK_SUBHEAD
    if not headline:
        return fallback
    return SlideCopy(headline=headline, subhead=subhead, source="claude")
