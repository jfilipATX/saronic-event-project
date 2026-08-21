"""T7 — audience estimation as an option slate. T7.5 — sanity band.

Follows the architectural rule: the provider **reports** a base number, this
module **classifies and explains**, the human **decides**.

Two jobs:

1. ``build_audience_options`` turns a single provider number into a bracketed
   slate (conservative / baseline / ambitious) so the coordinator picks a
   planning number with the trade-off visible.
2. ``sanity_check`` (T7.5) compares a number against the *actual* venue slate and
   states the problem in words — "6,000 exceeds every local venue except the
   Convention Center; consider multi-day, satellite overflow, or scoping down".
   The coordinator should never have to infer that from a wall of red badges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from app.db.models import DecisionOption
from app.providers.base import Venue

# ── Sanity states (T7.5) ──
SANITY_OK = "ok"
#: Fits nothing in the slate.
SANITY_EXCEEDS_ALL = "exceeds_all"
#: Fits only one venue — viable but fragile; losing that venue kills the plan.
SANITY_EXCEEDS_MOST = "exceeds_most"
#: So far under the smallest venue that the room will read as empty.
SANITY_UNDERSHOOTS = "undershoots"

#: Below this fraction of the smallest venue, the space reads as oversized.
UNDERSHOOT_RATIO = 0.25

#: Bracket multipliers applied to the provider's base estimate.
CONSERVATIVE_RATIO = 0.6
AMBITIOUS_RATIO = 1.3


@dataclass
class SanityResult:
    state: str
    insight: str
    fitting_venues: List[str] = field(default_factory=list)


def _fmt(n: int) -> str:
    return f"{n:,}"


def sanity_check(audience: int, venues: Sequence[Venue]) -> SanityResult:
    """Judge an audience number against the real venue slate.

    With no venues we cannot judge, and inventing a verdict would be worse than
    staying quiet — so we return OK with no insight.
    """
    if not venues:
        return SanityResult(SANITY_OK, "", [])

    fitting = [v for v in venues if v.capacity >= audience]
    fitting_names = [v.name for v in fitting]
    largest = max(venues, key=lambda v: v.capacity)
    smallest = min(venues, key=lambda v: v.capacity)

    if not fitting:
        return SanityResult(
            SANITY_EXCEEDS_ALL,
            (
                f"An estimate of {_fmt(audience)} exceeds every venue in this slate — "
                f"the largest, {largest.name}, holds {_fmt(largest.capacity)}. "
                "Consider a multi-day format, satellite overflow space, or scoping down "
                "the audience target before committing."
            ),
            [],
        )

    if len(fitting) == 1:
        only = fitting[0]
        return SanityResult(
            SANITY_EXCEEDS_MOST,
            (
                f"An estimate of {_fmt(audience)} exceeds all local venues except "
                f"{only.name} ({_fmt(only.capacity)}). That is workable but fragile: "
                "if it falls through there is no backup at this size. Consider holding "
                "a multi-day or satellite overflow option, or scoping down."
            ),
            fitting_names,
        )

    if audience > 0 and audience < smallest.capacity * UNDERSHOOT_RATIO:
        return SanityResult(
            SANITY_UNDERSHOOTS,
            (
                f"An estimate of {_fmt(audience)} is far below even the smallest venue "
                f"here ({smallest.name}, {_fmt(smallest.capacity)}). Every option will "
                "read as oversized and under-attended; consider a smaller room, a "
                "partitioned space, or a more intimate format."
            ),
            fitting_names,
        )

    return SanityResult(SANITY_OK, "", fitting_names)


def build_audience_options(
    base: int,
    city: str,
    event_type: str,
    venues: Optional[Sequence[Venue]] = None,
) -> List[DecisionOption]:
    """Bracket the provider's estimate into a decision slate.

    When ``venues`` is supplied, each option is additionally judged by T7.5 and
    carries its own sanity verdict — a conservative number can be perfectly sound
    while the baseline is not, and the coordinator needs to see that difference.
    """
    base = max(0, int(base))
    conservative = int(base * CONSERVATIVE_RATIO)
    ambitious = int(base * AMBITIOUS_RATIO)

    # Guarantee distinct, ordered numbers even for tiny/zero bases.
    if conservative >= base:
        conservative = max(0, base - 1)
    if ambitious <= base:
        ambitious = base + 1

    specs = [
        (
            "conservative",
            conservative,
            "Conservative",
            f"{int((1 - CONSERVATIVE_RATIO) * 100)}% below the modelled estimate. "
            "Right call for a first-year event, an unproven city, or when the "
            "venue deposit is non-refundable.",
            "conservative",
        ),
        (
            "baseline",
            base,
            "Modelled estimate",
            f"Provider estimate for a {event_type} in {city}, based on comparable "
            "events and local market size.",
            "provider",
        ),
        (
            "ambitious",
            ambitious,
            "Ambitious",
            f"{int((AMBITIOUS_RATIO - 1) * 100)}% above the modelled estimate. "
            "Plan to this only with confirmed demand signals — press coverage, "
            "partner co-marketing, or a returning audience.",
            "ambitious",
        ),
    ]

    options: List[DecisionOption] = []
    for key, number, label, why, basis in specs:
        data = {"audience": number, "basis": basis}
        reasoning = f"{_fmt(number)} attendees. {why}"

        if venues is not None:
            verdict = sanity_check(number, venues)
            data["sanity"] = verdict.state
            data["fitting_venues"] = verdict.fitting_venues
            if verdict.insight:
                reasoning += f" {verdict.insight}"

        options.append(
            DecisionOption(
                key=key,
                label=f"{label}: {_fmt(number)} attendees",
                reasoning=reasoning,
                data=data,
            )
        )
    return options
