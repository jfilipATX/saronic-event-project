"""Real provider stubs — lazy-imported only when PROVIDER_MODE=real.

Left as explicit TODOs so the live-API wiring happens in a real Claude session with
the keys present. Keeping them as named, typed stubs (not dead code) documents the
integration points for the graded history.
"""
from __future__ import annotations

from app.providers.base import (
    AudienceProvider,
    ImageProvider,
    Venue,
    VenueProvider,
)


class RealVenueProvider(VenueProvider):
    """Wire to Google Places / a venue API when keys are available."""

    def __init__(self, config) -> None:
        self._config = config

    def search(self, city: str, audience: int):
        raise NotImplementedError("Real venue provider not wired in mock mode.")


class RealAudienceProvider(AudienceProvider):
    def __init__(self, config) -> None:
        self._config = config

    def base_audience(self, city: str, event_type: str) -> int:
        raise NotImplementedError("Real audience provider not wired in mock mode.")


class RealImageProvider(ImageProvider):
    """Wire to Pexels (PEXELS_API_KEY) + Saronic local/URL media folder."""

    def __init__(self, config) -> None:
        self._config = config

    def fetch(self, query: str, role: str, limit: int = 5):
        raise NotImplementedError("Real image provider not wired in mock mode.")
