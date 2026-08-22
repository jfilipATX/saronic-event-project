"""Location line formatting (P5-3).

Saronic runs events across many states and countries, so a bare city is
ambiguous — Springfield, IL and Springfield, MA must never collapse into one.
The helper builds "City, ST, Country" with state and country abbreviated, and
drops any missing middle piece rather than emitting ", , US".

It is deliberately dumb: it formats whatever it is given. The US default is the
*caller's* responsibility (the Event model defaults country to "US"), because
the helper is also used by tests and non-event contexts where a default would
be wrong.
"""
from __future__ import annotations

from typing import Optional

#: Common country names -> ISO-3166 alpha-2 for a compact, locale-neutral line.
_COUNTRY_ABBR = {
    "united states": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "canada": "CA",
    "australia": "AU",
    "germany": "DE",
    "france": "FR",
    "japan": "JP",
    "singapore": "SG",
}


def location_line(city: Optional[str], state: Optional[str] = None,
                  country: Optional[str] = None) -> str:
    """Return "City, ST, CC" with missing middle pieces dropped.

    city is required-ish: a location with only a country and no city reads as
    just the country, not an empty leading fragment.
    """
    city = (city or "").strip()
    state = (state or "").strip()
    country = (country or "").strip()

    country = _COUNTRY_ABBR.get(country.lower(), country.upper()) if country else ""

    parts = [p for p in (city, state, country) if p]
    return ", ".join(parts)
