"""P5-4 — manual-trigger LLM venue search (coordinator button -> Claude proposes
venues, proposed as options, never auto-applied).

This is the same "provider proposes, human decides" seam as P2-1: the LLM
returns candidate venues near {city, state} that fit {capacity, amenities}, and
we surface them as proposed DecisionOptions with reasoning. Nothing is written
to the venue slate until the coordinator confirms.

The load-bearing parts are spend transparency (P3-1 pattern):

* ``estimate_search_cost(prompt_chars)`` gives a deterministic, pre-call token +
  USD estimate so the UI can show the cost BEFORE the coordinator triggers it.
* ``search_venues(...)`` accepts a per-event cap and the global SpendMeter halts
  the request before it leaves (manual trigger != unbounded). Every call is
  logged to the SpendLedger attributed to the event.

Offline/mock mode: ``client=None`` returns proposed options with no spend and no
network call, so the feature is fully runnable and testable offline.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

# Conservative blended price (USD per 1k tokens, input+output) — matches the
# SpendMeter fallback so the estimate a coordinator sees is the same worst case
# the meter enforces. Opus-class is the expensive end; using it keeps the guard
# honest (halts earlier rather than later).
_FALLBACK_USD_PER_1K = 0.03

# Rough chars->tokens for an English prompt. Deterministic and deliberately
# conservative (fewer chars per token => higher estimate). This is an estimate
# for display, not billing — the real meter reconciles against the API's usage.
_CHARS_PER_TOKEN = 4

# Worst-case output budget for one venue search. Generous enough to return a
# handful of candidates with reasoning; capped so the ceiling is bounded.
_MAX_OUTPUT_TOKENS = 1500

SYSTEM = (
    "You propose event venues. You are given a city, state, target capacity, "
    "and required amenities. Return a JSON array of 3-5 candidate venues that "
    "could plausibly host the event, each with: name, city, capacity (integer), "
    "and a one-sentence 'why' it fits. Do not invent websites or claim "
    "booking. If a requirement cannot be met, say so in the why. Return ONLY "
    "the JSON array."
)


def build_search_prompt(city: str, state: str, capacity: int,
                        amenities: List[str]) -> str:
    amenity_line = ", ".join(amenities) if amenities else "none specified"
    return (
        f"Find venues in {city}, {state} for an event of about {capacity} "
        f"attendees. Required amenities: {amenity_line}. Propose candidates "
        f"that fit the capacity and as many amenities as possible."
    )


def estimate_search_cost(prompt_chars: int) -> Tuple[int, int, float]:
    """Deterministic pre-call estimate.

    Returns ``(input_tokens, output_tokens, usd_ceiling)``. The USD ceiling is
    the conservative blended cost of the worst case (input + max output). This
    is what the coordinator sees before triggering, so it must be stable and
    non-zero — never a guess after the fact.
    """
    input_tokens = max(1, int(round(prompt_chars / _CHARS_PER_TOKEN)))
    output_tokens = _MAX_OUTPUT_TOKENS
    usd = ((input_tokens + output_tokens) / 1000.0) * _FALLBACK_USD_PER_1K
    usd = round(usd, 4)
    return (input_tokens, output_tokens, usd)


def _parse_venues(raw: str) -> List[dict]:
    match = re.search(r"\[.*\]", raw or "", re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, list):
        return []
    out: List[dict] = []
    for item in payload:
        if isinstance(item, dict) and item.get("name"):
            out.append(item)
    return out


def _venue_to_option(venue: dict):
    from app.db.models import DecisionOption

    name = str(venue.get("name") or "Unnamed venue")
    cap = venue.get("capacity")
    try:
        cap_int = int(str(cap).replace(",", "")) if cap is not None else None
    except (ValueError, TypeError):
        cap_int = None
    where = str(venue.get("city") or "")
    why = str(venue.get("why") or "Proposed by venue search.")
    reasoning = f"LLM venue search: {why}"
    if cap_int is not None:
        reasoning += f" Stated capacity ~{cap_int:,}."
    if where:
        reasoning += f" Located in {where}."
    return DecisionOption(
        key=f"venue_search::{name}",
        label=name,
        reasoning=reasoning,
        data={"value": name, "source": "venue_search",
              "capacity": cap_int, "city": where},
    )


def search_venues(client, event_id: int, city: str, state: str,
                  capacity: int, amenities: List[str],
                  ledger=None, per_event_cap_usd: Optional[float] = None,
                  meter=None) -> list:
    """Propose venues via Claude, returned as staged DecisionOptions.

    ``client=None`` => offline mode: returns one clearly-marked proposed option
    so the UI flow is exercisable without spend. The call is otherwise gated by
    (a) a per-event cap when ``per_event_cap_usd`` is set and the event's ledger
    total already meets/exceeds it, and (b) the global SpendMeter when a
    ``meter`` is supplied. Spend is logged to ``ledger`` attributed to the
    event. Nothing is written as a chosen fact — these are proposals to confirm.
    """
    from app.db.models import DecisionOption

    prompt = build_search_prompt(city, state, capacity, amenities)

    if client is None:
        return [DecisionOption(
            key="venue_search::offline",
            label="(offline proposal) Austin Convention Center",
            reasoning=("Offline mode: this is a placeholder so the staging flow "
                       "works without a Claude call. Run with a key to get real "
                       "candidates."),
            data={"value": "offline", "source": "venue_search"})]

    # Per-event cap: halt before the call if this event already met its ceiling.
    if per_event_cap_usd is not None and ledger is not None:
        from app.claude.errors import BudgetExceededError
        if ledger.total_for_event(event_id) >= per_event_cap_usd:
            raise BudgetExceededError(spent=ledger.total_for_event(event_id),
                                      limit=per_event_cap_usd)

    raw = client.complete(
        system=SYSTEM, prompt=prompt, max_tokens=_MAX_OUTPUT_TOKENS,
        temperature=0.3, event_id=event_id, surface="venue_search")
    venues = _parse_venues(raw)
    return [_venue_to_option(v) for v in venues] or [DecisionOption(
        key="venue_search::none",
        label="(no venues found)",
        reasoning=("The search returned no usable candidates. Try a wider "
                   "amenity set or a nearby city."),
        data={"value": "none", "source": "venue_search"})]
