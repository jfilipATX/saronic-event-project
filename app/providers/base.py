"""Provider Protocols — the swappable interface boundary.

Every external dependency (venues, audience data, stock imagery, Saronic media, and
Claude itself) sits behind one of these Protocols. The app depends on the Protocol,
never the implementation, so we can ship mock-by-default and swap in real APIs via a
single config flag (PROVIDER_MODE / USE_REAL_CLAUDE) with no feature-code changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, runtime_checkable


@dataclass
class Venue:
    name: str
    city: str
    capacity: int
    rating: float
    notes: str = ""


@dataclass
class ImageAsset:
    url: str
    role: str          # "city-stock" | "saronic-product" | ...
    caption: str = ""


@runtime_checkable
class VenueProvider(Protocol):
    def search(self, city: str, audience: int) -> List[Venue]:
        ...


@runtime_checkable
class AudienceProvider(Protocol):
    def base_audience(self, city: str, event_type: str) -> int:
        ...


@runtime_checkable
class ImageProvider(Protocol):
    def fetch(self, query: str, role: str, limit: int = 5) -> List[ImageAsset]:
        ...
