"""Provider Protocols — the swappable interface boundary.

Every external dependency (venues, audience data, stock imagery, Saronic media, and
Claude itself) sits behind one of these Protocols. The app depends on the Protocol,
never the implementation, so we can ship mock-by-default and swap in real APIs via a
single config flag (PROVIDER_MODE / USE_REAL_CLAUDE) with no feature-code changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, runtime_checkable


@dataclass
class Venue:
    name: str
    city: str
    capacity: int
    rating: float
    notes: str = ""
    #: Optional enrichment (P2-3). A provider supplies what it knows; the
    #: presentation layer degrades gracefully for whatever is missing.
    website: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: Stable identity for favourites/history. Defaults to a slug of the name +
    #: city, but a provider may pin it so a RENAMED venue keeps its history.
    venue_ref: Optional[str] = None


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
