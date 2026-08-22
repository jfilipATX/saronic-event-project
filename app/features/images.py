"""T10 — image role resolution (press-kit asset contract).

Templates request images by **role**, never by filename, per DESIGN.md. This
module answers those roles.

The load-bearing rule is **brand-first**: roles that represent Saronic itself
resolve to owned press-kit files and are structurally incapable of falling
through to stock. Only context roles (city/venue) consult the stock provider.
Putting a generic stock boat where we have a real Corsair hero shot would be a
downgrade, so the vocabulary is split into two disjoint sets rather than relying
on a lookup order someone could later reorder by accident.

Brand assets are local files with no API key and no network, so they keep working
when Pexels is down, rate-limited, or unconfigured — the deck degrades to
"no city photo", never to "no logo".
"""
from __future__ import annotations

import os
from typing import Optional, Protocol

from app.providers.base import ImageAsset

#: Root of the press-kit assets.
#:
#: Defaults to the copy bundled in the repo (``assets/press-kit``) so a fresh
#: clone renders correctly on any machine with no configuration. Override with
#: SARONIC_PRESS_KIT to point at a full press-kit checkout elsewhere.
_BUNDLED_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "press-kit",
)
PRESS_KIT_ROOT = os.environ.get("SARONIC_PRESS_KIT", _BUNDLED_ROOT)

#: role -> path relative to the press-kit root. Mirrors the DESIGN.md table.
_BRAND_ASSETS: dict[str, str] = {
    "logo-on-dark": "Logos/Saronic_Logo_Full--Light.png",
    "logo-on-light": "Logos/Saronic_Logo_Full--Dark.png",
    "mark-on-dark": "Logos/Saronic_Logo_Symbol--Light.png",
    "mark-on-light": "Logos/Saronic_Logo_Symbol--Dark.png",
    "hero-16x9": "Images/Corsair/SAR_Corsair_Hero.png",
    "imagery-alt": "Images/Corsair/Saronic_Corsair.jpg",
}

#: Roles served by owned Saronic assets. Never delegated to stock.
BRAND_ROLES = frozenset(_BRAND_ASSETS)

#: Roles where we genuinely have no owned asset and stock is the right answer.
STOCK_ROLES = frozenset({"city-stock", "venue-stock"})

_CAPTIONS = {
    "logo-on-dark": "Saronic full wordmark, light lockup",
    "logo-on-light": "Saronic full wordmark, dark lockup",
    "mark-on-dark": "Saronic symbol, light",
    "mark-on-light": "Saronic symbol, dark",
    "hero-16x9": "Saronic Corsair, hero",
    "imagery-alt": "Saronic Corsair",
}


class UnknownRoleError(KeyError):
    """Raised for a role outside the contract.

    Deliberately loud: a typo'd role silently returning ``None`` would surface
    as a blank slide in front of an audience, which is the worst possible place
    to discover it.
    """


class StockProvider(Protocol):
    def fetch(self, query: str, role: str, limit: int = 5) -> list[ImageAsset]:
        ...


class ImageResolver:
    """Resolves a design role to a concrete asset, brand-first."""

    def __init__(
        self,
        stock_provider: Optional[StockProvider] = None,
        press_kit_root: str = PRESS_KIT_ROOT,
    ) -> None:
        self._stock = stock_provider
        self._root = press_kit_root

    def resolve(self, role: str, city: str = "") -> Optional[ImageAsset]:
        """Return the asset for ``role``, or ``None`` if only stock could serve it
        and stock is unavailable. Raises ``UnknownRoleError`` for bad roles."""
        if role in BRAND_ROLES:
            return self._brand(role)
        if role in STOCK_ROLES:
            return self._stock_asset(role, city)
        raise UnknownRoleError(
            f"{role!r} is not a known image role. Brand roles: "
            f"{sorted(BRAND_ROLES)}; stock roles: {sorted(STOCK_ROLES)}."
        )

    def _brand(self, role: str) -> ImageAsset:
        path = os.path.join(self._root, _BRAND_ASSETS[role])
        return ImageAsset(url=path, role=role, caption=_CAPTIONS.get(role, ""))

    def _stock_asset(self, role: str, city: str) -> Optional[ImageAsset]:
        if self._stock is None:
            return None
        try:
            results = self._stock.fetch(city, role, limit=1)
        except Exception:
            # A stock outage must never take the deck down; the template falls
            # back to a solid brand surface instead of a photo.
            return None
        return results[0] if results else None
