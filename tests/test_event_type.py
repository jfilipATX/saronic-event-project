"""T8 — event type as an option slate.

Same architectural rule: full slate, reasoning on every option, never pre-filter.
The event type drives audience modelling and venue sizing downstream, so the
coordinator needs the trade-offs visible at the moment they choose.
"""
from __future__ import annotations

import pytest

from app.features.event_type import EVENT_TYPES, build_event_type_options


class TestBuildEventTypeOptions:
    def test_all_supported_types_are_offered(self):
        opts = build_event_type_options()
        assert {o.key for o in opts} == set(EVENT_TYPES)

    def test_nothing_is_filtered_even_with_a_city_hint(self):
        assert len(build_event_type_options(city="Austin")) == len(EVENT_TYPES)

    def test_every_option_states_scale_and_trade_off(self):
        for o in build_event_type_options():
            assert o.reasoning.strip()
            assert "typical_scale" in o.data

    def test_convention_is_the_largest_scale(self):
        opts = {o.key: o for o in build_event_type_options()}
        assert opts["convention"].data["typical_scale"] > opts["panel"].data["typical_scale"]

    def test_options_are_ordered_largest_scale_first(self):
        scales = [o.data["typical_scale"] for o in build_event_type_options()]
        assert scales == sorted(scales, reverse=True)

    def test_labels_are_human_readable_not_slugs(self):
        assert all(o.label and "_" not in o.label for o in build_event_type_options())

    def test_city_hint_is_woven_into_reasoning_when_given(self):
        opts = build_event_type_options(city="Austin")
        assert any("Austin" in o.reasoning for o in opts)

    def test_keys_match_the_mock_audience_provider_vocabulary(self):
        """Downstream the chosen key is fed to AudienceProvider.base_audience();
        a vocabulary mismatch there silently degrades to the 'other' default."""
        from app.providers.mock.providers import MockAudienceProvider

        provider = MockAudienceProvider()
        fallback = provider.base_audience("Austin", "definitely-not-a-type")
        specific = [
            provider.base_audience("Austin", key)
            for key in EVENT_TYPES
            if key != "other"
        ]
        assert all(v != fallback for v in specific), (
            "every event type key must be recognised by the audience provider"
        )
