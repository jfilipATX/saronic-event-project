"""Venue fit classification — the presentation-layer half of the provider fix.

The provider hands back every venue; THIS module decides how to *show* fit to the
coordinator. Per the design contract: three states, sort by fit, never hide, and
always carry a reasoning line explaining why a flagged venue is still on the table.
"""
from __future__ import annotations

import pytest

from app.features.venue_options import (
    FIT_FITS,
    FIT_TIGHT,
    FIT_UNDER,
    build_venue_options,
    classify_fit,
)
from app.providers.base import Venue


class TestClassifyFit:
    def test_capacity_at_or_above_estimate_fits(self):
        assert classify_fit(capacity=9000, audience=6000) == FIT_FITS
        assert classify_fit(capacity=6000, audience=6000) == FIT_FITS

    def test_slightly_under_is_tight_not_rejected(self):
        # 5700 / 6000 = 95% — workable with standing room or staggered sessions.
        assert classify_fit(capacity=5700, audience=6000) == FIT_TIGHT

    def test_well_under_is_flagged_under_capacity(self):
        assert classify_fit(capacity=3000, audience=6000) == FIT_UNDER

    def test_tight_boundary_is_inclusive_at_ninety_percent(self):
        assert classify_fit(capacity=5400, audience=6000) == FIT_TIGHT
        assert classify_fit(capacity=5399, audience=6000) == FIT_UNDER

    def test_zero_or_unknown_audience_cannot_judge_fit(self):
        """With no estimate yet, everything 'fits' — we must not invent a verdict."""
        assert classify_fit(capacity=100, audience=0) == FIT_FITS


class TestBuildVenueOptions:
    @pytest.fixture()
    def venues(self) -> list[Venue]:
        return [
            Venue("Palmer Events Center", "Austin", 3000, 4.4, "Lady Bird Lake views."),
            Venue("Austin Convention Center", "Austin", 9000, 4.6, "Downtown."),
            Venue("Tight Hall", "Austin", 5700, 4.0, "Historic."),
        ]

    def test_every_venue_becomes_an_option_none_are_hidden(self, venues):
        opts = build_venue_options(venues, audience=6000)
        assert len(opts) == 3

    def test_options_are_sorted_best_fit_first(self, venues):
        opts = build_venue_options(venues, audience=6000)
        assert [o.label for o in opts] == [
            "Austin Convention Center",  # fits
            "Tight Hall",                # tight
            "Palmer Events Center",      # under
        ]

    def test_each_option_carries_its_fit_state_for_the_badge(self, venues):
        opts = build_venue_options(venues, audience=6000)
        by_label = {o.label: o for o in opts}
        assert by_label["Austin Convention Center"].data["fit"] == FIT_FITS
        assert by_label["Tight Hall"].data["fit"] == FIT_TIGHT
        assert by_label["Palmer Events Center"].data["fit"] == FIT_UNDER

    def test_under_capacity_option_explains_why_it_is_still_offered(self, venues):
        opts = build_venue_options(venues, audience=6000)
        palmer = next(o for o in opts if o.label == "Palmer Events Center")
        assert "50% under capacity" in palmer.reasoning
        assert "still offered" in palmer.reasoning.lower()

    def test_fitting_option_reasoning_states_the_headroom(self, venues):
        opts = build_venue_options(venues, audience=6000)
        acc = next(o for o in opts if o.label == "Austin Convention Center")
        assert "9,000" in acc.reasoning and "6,000" in acc.reasoning

    def test_keys_are_stable_and_url_safe(self, venues):
        opts = build_venue_options(venues, audience=6000)
        assert {o.key for o in opts} == {
            "austin-convention-center", "palmer-events-center", "tight-hall",
        }

    def test_ties_preserve_provider_order(self):
        vs = [
            Venue("Beta Hall", "Austin", 9000, 4.1, ""),
            Venue("Alpha Hall", "Austin", 9000, 4.9, ""),
        ]
        assert [o.label for o in build_venue_options(vs, audience=100)] == [
            "Beta Hall", "Alpha Hall",
        ]

    def test_empty_slate_yields_no_options(self):
        assert build_venue_options([], audience=6000) == []
