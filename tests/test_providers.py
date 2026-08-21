"""Provider contract tests.

Core rule (T11.5): a provider REPORTS, it never DECIDES. Filtering a venue out
because it looks too small is a decision — and it is the coordinator's to make,
possibly for budget reasons the provider knows nothing about.
"""
from __future__ import annotations

import pytest

from app.config import Config
from app.providers.base import Venue
from app.providers.mock.providers import MockVenueProvider
from app.providers.registry import (
    get_audience_provider,
    get_image_provider,
    get_venue_provider,
)


class TestVenueProviderReturnsFullSlate:
    def test_search_returns_every_venue_in_the_city_regardless_of_capacity(self):
        venues = MockVenueProvider().search("Austin", audience=6000)
        names = {v.name for v in venues}
        # Palmer (3000) is under a 6000 estimate but must still be offered:
        # the coordinator may downsize the event to fit the budget.
        assert "Palmer Events Center" in names
        assert "Austin Convention Center" in names
        assert "Fairmont Austin" in names

    def test_slate_is_identical_no_matter_the_audience_argument(self):
        """No caller should need the audience=0 trick to see everything."""
        small = MockVenueProvider().search("Austin", audience=1)
        huge = MockVenueProvider().search("Austin", audience=999_999)
        assert [v.name for v in small] == [v.name for v in huge]

    def test_unknown_city_returns_empty_not_an_error(self):
        assert MockVenueProvider().search("Nowhere", audience=100) == []

    def test_search_is_case_and_whitespace_insensitive(self):
        assert MockVenueProvider().search("  SAN DIEGO ", audience=100)


class TestRegistryWiring:
    """The registry is the mock/real seam — a broken import path here means the
    app dies the moment PROVIDER_MODE flips, long after we stopped looking."""

    def test_mock_mode_is_the_default(self):
        assert isinstance(get_venue_provider(Config()), MockVenueProvider)

    def test_all_three_providers_resolve_in_mock_mode(self):
        cfg = Config()
        assert get_venue_provider(cfg) is not None
        assert get_audience_provider(cfg) is not None
        assert get_image_provider(cfg) is not None

    @pytest.mark.parametrize("getter", [get_venue_provider, get_audience_provider,
                                        get_image_provider])
    def test_real_mode_import_paths_resolve(self, getter):
        """Real providers are stubs, but the IMPORT must work — a typo'd module
        path is a deploy-time landmine, not a runtime TODO."""
        cfg = Config(provider_mode="real")
        provider = getter(cfg)  # must not raise ImportError/ModuleNotFoundError
        assert provider is not None
