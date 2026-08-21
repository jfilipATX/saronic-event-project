"""Provider registry — the single place that selects mock vs real implementations.

Features call ``get_*_provider(config)`` and depend only on the Protocol, not the
concrete class. This is what makes "mock-default, real-opt-in" structural: flip
PROVIDER_MODE=real (after keys are present) and the same feature code runs live.
"""
from __future__ import annotations

from typing import Optional

from app.config import Config
from app.providers.base import (
    AudienceProvider,
    ImageProvider,
    VenueProvider,
)
from app.providers.mock.providers import (
    MockAudienceProvider,
    MockImageProvider,
    MockVenueProvider,
)


def get_venue_provider(config: Optional[Config] = None) -> VenueProvider:
    config = config or Config()
    if config.provider_mode == "real":
        from app.providers.real.providers import RealVenueProvider  # lazy import
        return RealVenueProvider(config)
    return MockVenueProvider()


def get_audience_provider(config: Optional[Config] = None) -> AudienceProvider:
    config = config or Config()
    if config.provider_mode == "real":
        from app.providers.real.providers import RealAudienceProvider
        return RealAudienceProvider(config)
    return MockAudienceProvider()


def get_image_provider(config: Optional[Config] = None) -> ImageProvider:
    config = config or Config()
    if config.provider_mode == "real":
        from app.providers.real.providers import RealImageProvider
        return RealImageProvider(config)
    return MockImageProvider()
