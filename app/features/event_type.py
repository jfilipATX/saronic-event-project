"""T8 — event type option slate.

The chosen key flows into ``AudienceProvider.base_audience()`` and then into
venue sizing, so the vocabulary here is load-bearing: it must match the provider's
recognised types or estimates silently fall back to the generic default. That
coupling is pinned by a test rather than a comment.
"""
from __future__ import annotations

from typing import List, Optional

from app.db.models import DecisionOption

#: key -> (label, typical scale, trade-off reasoning)
_EVENT_TYPES: dict[str, tuple[str, int, str]] = {
    "convention": (
        "Convention",
        5000,
        "Widest reach and strongest press draw, but the highest cost and the "
        "longest lead time — venue and exhibitor contracts land months ahead.",
    ),
    "company-hosted": (
        "Company-hosted event",
        800,
        "Full control of the room, the narrative, and the guest list. Costs sit "
        "with us rather than a host organisation, and attendance depends entirely "
        "on our own outreach.",
    ),
    "panel": (
        "Panel",
        250,
        "Cheapest and fastest to stand up, with the tightest, most qualified "
        "audience. Limited reach — this builds depth with people who already "
        "know us rather than breadth.",
    ),
    "other": (
        "Other / custom format",
        400,
        "Use when the format does not fit the standard shapes — a demo day, a "
        "customer dinner, a field demonstration. Audience modelling falls back to "
        "a generic baseline, so expect to override the estimate manually.",
    ),
}

#: Public vocabulary, ordered largest scale first.
EVENT_TYPES: tuple[str, ...] = tuple(
    sorted(_EVENT_TYPES, key=lambda k: _EVENT_TYPES[k][1], reverse=True)
)


def build_event_type_options(city: Optional[str] = None) -> List[DecisionOption]:
    """Offer every supported event type, largest scale first.

    Nothing is filtered — a panel is a legitimate choice in a big market and a
    convention is legitimate in a small one; that call belongs to the coordinator.
    """
    options: List[DecisionOption] = []
    for key in EVENT_TYPES:
        label, scale, why = _EVENT_TYPES[key]
        reasoning = f"Typically ~{scale:,} attendees. {why}"
        if city:
            reasoning += f" Scale is modelled against {city} market size."
        options.append(
            DecisionOption(
                key=key,
                label=label,
                reasoning=reasoning,
                data={"typical_scale": scale},
            )
        )
    return options
