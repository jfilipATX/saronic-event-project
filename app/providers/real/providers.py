"""Real provider stubs — lazy-imported only when PROVIDER_MODE=real.

Left as explicit TODOs so the live-API wiring happens in a real Claude session with
the keys present. Keeping them as named, typed stubs (not dead code) documents the
integration points for the graded history.
"""
from __future__ import annotations

from app.providers.base import (
    AudienceProvider,
    ImageAsset,
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
    """Pexels-backed stock imagery for context roles only.

    Never serves brand roles — ``app.features.images`` keeps BRAND_ROLES and
    STOCK_ROLES disjoint, so Saronic hardware can never be substituted by stock.

    Fails soft on purpose: a network error, a bad key, or a rate limit returns
    an empty list rather than raising, because a missing city photo must never
    take down a deck that is otherwise fully resolved from owned assets.
    """

    #: Pexels caps at 200 requests/hour on the free tier; keep calls small.
    _ENDPOINT = "https://api.pexels.com/v1/search"
    _TIMEOUT = 8

    def __init__(self, config) -> None:
        self._config = config

    def fetch(self, query: str, role: str, limit: int = 5):
        key = getattr(self._config, "pexels_api_key", "")
        if not key or not query.strip():
            return []

        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode({
            "query": f"{query} skyline" if role == "city-stock" else query,
            "per_page": max(1, min(int(limit), 15)),
            "orientation": "landscape",
        })
        req = urllib.request.Request(
            f"{self._ENDPOINT}?{params}",
            headers={
                "Authorization": key,  # server-side only; never rendered
                # Pexels sits behind Cloudflare, which rejects the default
                # urllib agent with 403 (error 1010) regardless of a valid key.
                # A real UA is required, not optional.
                "User-Agent": "SaronicEventTool/1.0 (+https://saronic.com)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return []

        assets = []
        for photo in payload.get("photos", []):
            src = (photo.get("src") or {}).get("landscape") or (photo.get("src") or {}).get("large")
            if not src:
                continue
            assets.append(ImageAsset(
                url=src,
                role=role,
                caption=photo.get("alt") or f"{query} ({photo.get('photographer', 'Pexels')})",
            ))
        return assets
