"""Mock (offline, free) providers — seed data so the prototype runs with zero
external calls and zero spend. These are intentionally deterministic so tests are
stable; the README documents how each maps to a real API.
"""
from __future__ import annotations

from app.providers.base import (
    AudienceProvider,
    ImageAsset,
    ImageProvider,
    Venue,
    VenueProvider,
)

_MOCK_VENUES = {
    "austin": [
        Venue("Austin Convention Center", "Austin", 9000, 4.6,
              "Downtown, 900k sqft exhibit hall.",
              website="https://www.austinconventioncenter.com",
              latitude=30.2639, longitude=-97.7397),
        Venue("Palmer Events Center", "Austin", 3000, 4.4, "Lady Bird Lake views.",
              website="https://www.palmereventscenter.com",
              latitude=30.2578, longitude=-97.7545),
        Venue("Fairmont Austin", "Austin", 1800, 4.5, "Rooftop ballroom, hotel.",
              website="https://www.fairmont.com/austin",
              latitude=30.2635, longitude=-97.7412),
    ],
    "san diego": [
        Venue("San Diego Convention Center", "San Diego", 7500, 4.7,
              "Waterfront, bay view.",
              website="https://www.visitsandiego.com",
              latitude=32.7061, longitude=-117.1624),
        Venue("Omni San Diego", "San Diego", 2200, 4.5, "Petco Park adjacent."),
    ],
}

_CITY_STOCK = {
    "austin": "https://images.example.com/stock/austin-skyline.jpg",
    "san diego": "https://images.example.com/stock/san-diego-bay.jpg",
}


class MockVenueProvider(VenueProvider):
    """Reports every venue in the city. Deliberately does NOT filter by capacity.

    Filtering here would be the provider making a decision that belongs to the
    human coordinator — a 3,000-seat hall is a perfectly valid answer to a 6,000
    estimate if the budget just got cut. Fit is classified and surfaced by
    ``app.features.venue_options``, which flags rather than hides.
    """

    def search(self, city: str, audience: int) -> list[Venue]:
        return list(_MOCK_VENUES.get(city.strip().lower(), []))


class MockAudienceProvider(AudienceProvider):
    def base_audience(self, city: str, event_type: str) -> int:
        base = {
            "convention": 5000,
            "company-hosted": 800,
            "panel": 250,
            "other": 400,
        }.get(event_type.strip().lower(), 400)
        # City size multiplier (mock heuristic).
        mult = 1.2 if city.strip().lower() in {"austin", "san diego"} else 1.0
        return int(base * mult)


class MockImageProvider(ImageProvider):
    def fetch(self, query: str, role: str, limit: int = 5) -> list[ImageAsset]:
        city = query.lower()
        if role == "city-stock" and city in _CITY_STOCK:
            return [ImageAsset(_CITY_STOCK[city], "city-stock", f"{query} skyline")]
        if role == "saronic-product":
            return [ImageAsset(
                "https://images.example.com/saronic/placeholder-product.png",
                "saronic-product", "Saronic product (placeholder)")]
        return []
