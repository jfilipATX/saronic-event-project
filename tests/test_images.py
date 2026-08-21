"""T10 — image role resolution.

The design contract: templates request images by **role**, never by filename.
The resolver's job is to answer a role with the right asset, and the ordering
rule is brand-first — real Saronic hardware carries the deck, and generic stock
only fills context roles (city/venue) where we have no owned asset.

A stock boat where we have a real Corsair shot is a downgrade, so the resolver is
built to make that substitution impossible rather than merely discouraged.
"""
from __future__ import annotations

import pytest

from app.features.images import (
    BRAND_ROLES,
    STOCK_ROLES,
    ImageResolver,
    UnknownRoleError,
)
from app.providers.base import ImageAsset


class _StubStockProvider:
    """Stands in for Pexels; records whether it was consulted at all."""

    def __init__(self, assets=None):
        self.calls = []
        self._assets = assets if assets is not None else [
            ImageAsset("https://stock.example/austin.jpg", "city-stock", "Austin skyline")
        ]

    def fetch(self, query: str, role: str, limit: int = 5):
        self.calls.append((query, role, limit))
        return list(self._assets)


@pytest.fixture()
def stock() -> _StubStockProvider:
    return _StubStockProvider()


@pytest.fixture()
def resolver(stock) -> ImageResolver:
    return ImageResolver(stock_provider=stock)


class TestBrandRolesNeverHitStock:
    @pytest.mark.parametrize("role", sorted(BRAND_ROLES))
    def test_brand_role_resolves_from_the_press_kit(self, resolver, stock, role):
        asset = resolver.resolve(role, city="Austin")
        assert asset is not None
        assert asset.role == role
        assert stock.calls == [], f"{role} must never consult the stock provider"

    def test_hero_resolves_to_the_real_corsair_hero_shot(self, resolver):
        asset = resolver.resolve("hero-16x9", city="Austin")
        assert "Corsair" in asset.url

    def test_brand_role_still_resolves_when_stock_provider_is_absent(self):
        """Brand assets are owned files — they must not depend on an API key."""
        offline = ImageResolver(stock_provider=None)
        assert offline.resolve("logo-on-dark", city="Austin") is not None

    def test_brand_asset_survives_a_broken_stock_provider(self):
        class Exploding:
            def fetch(self, *a, **k):
                raise RuntimeError("Pexels is down")

        r = ImageResolver(stock_provider=Exploding())
        assert r.resolve("mark-on-light", city="Austin") is not None


class TestStockRolesFillContextOnly:
    def test_city_stock_consults_the_stock_provider(self, resolver, stock):
        asset = resolver.resolve("city-stock", city="Austin")
        assert asset.url.startswith("https://stock.example")
        assert stock.calls and stock.calls[0][0] == "Austin"

    def test_stock_role_returns_none_when_provider_is_absent(self):
        offline = ImageResolver(stock_provider=None)
        assert offline.resolve("city-stock", city="Austin") is None

    def test_stock_failure_degrades_to_none_not_an_exception(self):
        class Exploding:
            def fetch(self, *a, **k):
                raise RuntimeError("Pexels is down")

        r = ImageResolver(stock_provider=Exploding())
        assert r.resolve("city-stock", city="Austin") is None

    def test_stock_miss_returns_none(self):
        r = ImageResolver(stock_provider=_StubStockProvider(assets=[]))
        assert r.resolve("city-stock", city="Nowhere") is None


class TestRoleVocabulary:
    def test_brand_and_stock_roles_do_not_overlap(self):
        assert BRAND_ROLES.isdisjoint(STOCK_ROLES)

    def test_unknown_role_is_a_loud_error_not_a_silent_none(self):
        r = ImageResolver(stock_provider=None)
        with pytest.raises(UnknownRoleError, match="hero-4x3"):
            r.resolve("hero-4x3", city="Austin")

    def test_every_brand_role_in_design_md_is_implemented(self):
        """DESIGN.md is the contract; drift between it and code is a real bug."""
        assert BRAND_ROLES == {
            "logo-on-dark", "logo-on-light", "mark-on-dark", "mark-on-light",
            "hero-16x9", "imagery-alt",
        }


class TestAssetsExistOnDisk:
    @pytest.mark.parametrize("role", sorted(BRAND_ROLES))
    def test_resolved_brand_asset_points_at_a_real_file(self, resolver, role):
        import os

        asset = resolver.resolve(role, city="Austin")
        assert os.path.isfile(asset.url), f"{role} -> missing file {asset.url}"
