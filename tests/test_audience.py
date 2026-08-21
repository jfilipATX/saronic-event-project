"""T7 + T7.5 — audience estimation as an option slate, with sanity band.

T7: the estimator offers a *range* of defensible audience numbers with reasoning,
never a single silent number.

T7.5: when the estimate sits badly against the actual venue slate, the estimator
says so in words as an option-level insight — the coordinator should not have to
infer "we have a problem" from a wall of red badges.
"""
from __future__ import annotations

import pytest

from app.features.audience import (
    SANITY_EXCEEDS_ALL,
    SANITY_EXCEEDS_MOST,
    SANITY_OK,
    SANITY_UNDERSHOOTS,
    build_audience_options,
    sanity_check,
)
from app.providers.base import Venue


def _austin_slate() -> list[Venue]:
    return [
        Venue("Austin Convention Center", "Austin", 9000, 4.6, "Downtown."),
        Venue("Palmer Events Center", "Austin", 3000, 4.4, "Lake views."),
        Venue("Fairmont Austin", "Austin", 1800, 4.5, "Hotel ballroom."),
    ]


class TestBuildAudienceOptions:
    def test_offers_multiple_defensible_numbers_not_one(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        assert len(opts) >= 3

    def test_provider_estimate_is_offered_and_labelled_as_the_baseline(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        baseline = next(o for o in opts if o.data["basis"] == "provider")
        assert baseline.data["audience"] == 5000
        assert "Austin" in baseline.reasoning

    def test_conservative_and_ambitious_bracket_the_baseline(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        numbers = sorted(o.data["audience"] for o in opts)
        assert numbers[0] < 5000 < numbers[-1]

    def test_every_option_carries_reasoning_for_the_human(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        assert all(o.reasoning.strip() for o in opts)

    def test_keys_are_stable_and_distinct(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        assert len({o.key for o in opts}) == len(opts)

    def test_zero_base_still_produces_a_usable_slate(self):
        opts = build_audience_options(base=0, city="Nowhere", event_type="other")
        assert opts and all(o.data["audience"] >= 0 for o in opts)


class TestSanityCheck:
    def test_estimate_within_venue_capacity_is_ok(self):
        result = sanity_check(audience=2500, venues=_austin_slate())
        assert result.state == SANITY_OK
        assert result.insight == ""

    def test_estimate_exceeding_every_venue_is_flagged_with_advice(self):
        result = sanity_check(audience=12000, venues=_austin_slate())
        assert result.state == SANITY_EXCEEDS_ALL
        assert "12,000" in result.insight
        assert "exceeds" in result.insight.lower()
        # Must offer a way forward, not just alarm.
        assert "multi-day" in result.insight or "satellite" in result.insight
        assert "scoping down" in result.insight

    def test_estimate_fitting_only_one_venue_names_that_venue(self):
        result = sanity_check(audience=6000, venues=_austin_slate())
        assert result.state == SANITY_EXCEEDS_MOST
        assert "Austin Convention Center" in result.insight
        assert result.fitting_venues == ["Austin Convention Center"]

    def test_estimate_far_below_smallest_venue_is_flagged_as_oversized(self):
        result = sanity_check(audience=50, venues=_austin_slate())
        assert result.state == SANITY_UNDERSHOOTS
        assert "oversized" in result.insight.lower() or "smaller" in result.insight.lower()

    def test_empty_venue_slate_cannot_be_judged(self):
        result = sanity_check(audience=6000, venues=[])
        assert result.state == SANITY_OK
        assert result.insight == ""

    def test_result_reports_which_venues_fit(self):
        result = sanity_check(audience=2500, venues=_austin_slate())
        assert set(result.fitting_venues) == {
            "Austin Convention Center", "Palmer Events Center",
        }


class TestSanityInsightReachesTheOptions:
    def test_options_can_be_annotated_with_the_sanity_insight(self):
        opts = build_audience_options(
            base=6000, city="Austin", event_type="convention",
            venues=_austin_slate(),
        )
        flagged = [o for o in opts if o.data.get("sanity") == SANITY_EXCEEDS_MOST]
        assert flagged, "the 6,000 option must carry its own sanity verdict"
        assert "Austin Convention Center" in flagged[0].reasoning

    def test_each_option_is_judged_on_its_own_number(self):
        """A conservative option may be fine even when the baseline is not.

        base=4000 -> conservative 2400 (fits ACC + Palmer = ok) while the
        baseline 4000 fits only the Convention Center (exceeds_most).
        """
        opts = build_audience_options(
            base=4000, city="Austin", event_type="convention",
            venues=_austin_slate(),
        )
        states = {o.data["audience"]: o.data["sanity"] for o in opts}
        assert states[2400] == SANITY_OK
        assert states[4000] == SANITY_EXCEEDS_MOST
        assert len(set(states.values())) > 1

    def test_without_a_venue_slate_options_carry_no_sanity_verdict(self):
        opts = build_audience_options(base=6000, city="Austin", event_type="convention")
        assert all(o.data.get("sanity") is None for o in opts)
