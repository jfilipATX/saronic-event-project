"""Venue option presentation — turns a raw provider slate into a decision slate.

Split of responsibility (deliberate):

* ``VenueProvider.search()`` **reports** — every venue in the city, no filtering.
* This module **classifies and orders** — flags fit, sorts best-fit first, and
  writes the reasoning line, but never removes an option.
* The human **decides** — the resulting options go into a ``Decision`` (T11.5).

Hiding an under-capacity venue would quietly make the coordinator's call for
them. A 3,000-seat hall against a 6,000 estimate is a legitimate choice when the
budget halves; our job is to make the trade-off legible, not to make it.
"""
from __future__ import annotations

from typing import List, Sequence

from app.db.models import DecisionOption
from app.providers.base import Venue

#: Fit states. These map 1:1 to the badge tokens in DESIGN.md — `fits` renders as
#: quiet steel text, `tight` as the warning token, `under` as the danger token
#: (text only; filled banners are reserved for scan states).
FIT_FITS = "fits"
FIT_TIGHT = "tight"
FIT_UNDER = "under"

#: A venue within 10% of the estimate is workable with standing room or staggered
#: sessions, so it is "tight" rather than "under" — a materially different
#: conversation for the coordinator.
TIGHT_THRESHOLD = 0.90

#: Sort weight: best fit first, but nothing is ever dropped.
_FIT_RANK = {FIT_FITS: 0, FIT_TIGHT: 1, FIT_UNDER: 2}


def classify_fit(capacity: int, audience: int) -> str:
    """Classify ``capacity`` against the planned ``audience``.

    With no audience estimate yet (0 or negative) we cannot judge fit, so we
    report ``fits`` rather than inventing a verdict the data does not support.
    """
    if audience <= 0:
        return FIT_FITS
    if capacity >= audience:
        return FIT_FITS
    if capacity >= audience * TIGHT_THRESHOLD:
        return FIT_TIGHT
    return FIT_UNDER


def _slug(name: str) -> str:
    return "-".join(name.lower().split())


def _reasoning(venue: Venue, audience: int, fit: str) -> str:
    head = f"Capacity {venue.capacity:,}"
    if audience > 0:
        head += f" vs estimate {audience:,}"

    if fit == FIT_FITS:
        if audience > 0:
            headroom = venue.capacity - audience
            body = f" — {headroom:,} seats of headroom."
        else:
            body = " — no audience estimate recorded yet."
    elif fit == FIT_TIGHT:
        shortfall = round((1 - venue.capacity / audience) * 100)
        body = (
            f" — {shortfall}% under capacity, but tight rather than unworkable: "
            "still offered because standing room or staggered sessions can absorb it."
        )
    else:
        shortfall = round((1 - venue.capacity / audience) * 100)
        body = (
            f" — {shortfall}% under capacity. Still offered because a smaller "
            "venue may be the right call on budget, or the event can be scoped down."
        )

    tail = f" Rated {venue.rating}."
    if venue.notes:
        tail += f" {venue.notes}"
    return head + body + tail


def build_venue_options(
    venues: Sequence[Venue], audience: int
) -> List[DecisionOption]:
    """Build the coordinator-facing option slate from a raw provider result.

    Sorted best-fit first; ties keep provider order (Python's sort is stable).
    Every input venue appears in the output — that invariant is test-enforced.
    """
    options: List[DecisionOption] = []
    for v in venues:
        fit = classify_fit(v.capacity, audience)
        options.append(
            DecisionOption(
                key=_slug(v.name),
                label=v.name,
                reasoning=_reasoning(v, audience, fit),
                data={
                    "capacity": v.capacity,
                    "rating": v.rating,
                    "city": v.city,
                    "fit": fit,
                },
            )
        )
    options.sort(key=lambda o: _FIT_RANK[o.data["fit"]])
    return options
