"""Add a venue by URL, with amenity extraction (P4-1).

Reuses the P2-1 stack: the SSRF guard validates the URL, the bounded fetcher
retrieves it, and extraction iterates a **declared allowlist** rather than the
model's response, so an invented field cannot reach a venue record.

The design decision specific to this feature is that **amenities are a closed
checklist with three states, not a boolean**. "Does it have in-house security?"
has three answers a coordinator can act on:

* ``yes``     — plan around it
* ``no``      — budget a vendor
* ``unknown`` — the page doesn't say; someone has to call

Collapsing ``unknown`` into ``no`` turns a research gap into a false negative
that a coordinator plans around, and collapsing it into ``yes`` is worse. The
gap is itself the useful output, so it is reported as a cost rather than hidden.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.db.models import DecisionOption
from app.providers.base import Venue

#: Declared venue fields. Extraction iterates THIS, never the model response.
VENUE_FIELDS: tuple[str, ...] = (
    "venue_name",
    "address",
    "city",
    "capacity",
    "website",
)

#: The amenity checklist. Closed by design: a coordinator needs the same
#: questions answered for every venue to compare them at all.
AMENITIES: tuple[str, ...] = (
    "catering",
    "alcohol_service",
    "security",
    "av_production",
    "parking",
    "loading_access",
    "wifi",
    "accessible",
)

AMENITY_LABELS = {
    "catering": "Catering",
    "alcohol_service": "Alcohol service",
    "security": "On-site security",
    "av_production": "A/V and production",
    "parking": "Parking",
    "loading_access": "Loading dock access",
    "wifi": "Wi-Fi",
    "accessible": "Step-free access",
}

FIELD_LABELS = {
    "venue_name": "Venue name",
    "address": "Address",
    "city": "City",
    "capacity": "Capacity",
    "website": "Website",
}

_ANSWERS = ("yes", "no", "unknown")

SYSTEM = (
    "You read a venue's own web page and report only what it states. You never "
    "infer, estimate, or fill gaps from general knowledge — a coordinator will "
    "book a room based on this. If the page does not state something, say so."
)


def build_prompt(markup: str, source_url: str) -> str:
    fields = ", ".join(VENUE_FIELDS)
    amenities = ", ".join(AMENITIES)
    return (
        f"This is the page at {source_url}.\n\n"
        f"Return ONE JSON object. Top-level keys, all optional: {fields}. "
        f"Omit any the page does not state — do not guess.\n"
        f"capacity must be a plain integer (maximum stated capacity).\n\n"
        f'Also return an "amenities" object with these keys: {amenities}.\n'
        f'Each value must be exactly "yes", "no", or "unknown". Use "unknown" '
        f'whenever the page does not clearly state it — "unknown" is a useful '
        f'answer and guessing is not.\n\n'
        f"Page content follows.\n\n{markup[:18000]}"
    )


def _coerce_capacity(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        # Only a clean number: "about 3000ish" is a guess wearing a number's
        # clothes, and a wrong capacity produces a wrong fit badge.
        text = value.strip().replace(",", "")
        if re.fullmatch(r"\d+", text):
            return int(text) or None
    return None


def _clean_amenities(raw: Any) -> Dict[str, str]:
    """Always returns every declared amenity. Unrecognised keys are discarded,
    unrecognised values become ``unknown``."""
    reported = raw if isinstance(raw, dict) else {}
    out: Dict[str, str] = {}
    for key in AMENITIES:
        value = str(reported.get(key, "")).strip().lower()
        out[key] = value if value in _ANSWERS else "unknown"
    return out


def extract_venue(client, markup: str, source_url: str,
                  event_id=None) -> Dict[str, Any]:
    """Ask Claude to read a venue page. Returns {} rather than raising."""
    if client is None:
        return {}
    try:
        raw = client.complete(
            system=SYSTEM,
            prompt=build_prompt(markup, source_url),
            max_tokens=1500,
            temperature=0.0,
            event_id=event_id,
            surface="venue_scrape",
        )
    except Exception:  # noqa: BLE001 - a scrape failure is never fatal
        return {}

    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except (ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    facts: Dict[str, Any] = {}
    for field in VENUE_FIELDS:  # allowlist, not payload.keys()
        if field not in payload:
            continue
        value = payload[field]
        if field == "capacity":
            capacity = _coerce_capacity(value)
            if capacity is not None:
                facts[field] = capacity
            continue
        text = str(value).strip()
        if text:
            facts[field] = text
    facts["amenities"] = _clean_amenities(payload.get("amenities"))
    return facts


def amenity_summary(amenities: Dict[str, str]) -> str:
    """One line a coordinator can act on.

    Names the gaps explicitly: 'not stated' is a task for someone, and burying
    it reads as 'no' to anyone skimming.
    """
    have = [AMENITY_LABELS[k] for k in AMENITIES if amenities.get(k) == "yes"]
    lack = [AMENITY_LABELS[k] for k in AMENITIES if amenities.get(k) == "no"]
    gaps = [AMENITY_LABELS[k] for k in AMENITIES if amenities.get(k, "unknown")
            == "unknown"]

    if not have and not lack:
        # The page said nothing usable. Say that plainly rather than listing
        # eight "not stated" items, which reads like data.
        return "Nothing stated about amenities on this page — all eight need confirming."
    parts: List[str] = []
    if have:
        parts.append("Has " + ", ".join(h.lower() for h in have))
    if lack:
        parts.append("no " + ", ".join(l.lower() for l in lack)
                     + " (budget a vendor)")
    if gaps:
        parts.append("not stated: " + ", ".join(g.lower() for g in gaps))
    return ". ".join(parts) + "."


def _ref_from_url(source_url: str) -> str:
    """Stable id anchored on the URL, not the display name.

    A venue that rebrands keeps its favourites and history; a name slug would
    silently orphan them.
    """
    cleaned = re.sub(r"^https?://(www\.)?", "", (source_url or "").strip())
    cleaned = cleaned.split("/")[0].lower()
    return "url-" + re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")


def venue_from_facts(facts: Dict[str, Any], source_url: str) -> Venue:
    """Turn confirmed facts into a Venue. Raises if it cannot be rated."""
    capacity = _coerce_capacity(facts.get("capacity"))
    if capacity is None:
        raise ValueError(
            "A venue needs a capacity — without it the fit against your "
            "audience cannot be judged, which is the whole point of the slate."
        )
    amenities = _clean_amenities(facts.get("amenities"))
    return Venue(
        name=str(facts.get("venue_name") or "Untitled venue"),
        city=str(facts.get("city") or ""),
        capacity=capacity,
        rating=0.0,  # no rating source for a self-reported page
        notes=amenity_summary(amenities),
        website=str(facts.get("website") or source_url),
        venue_ref=_ref_from_url(source_url),
    )


def build_venue_options(facts: Dict[str, Any], source_url: str) -> List[DecisionOption]:
    """Prefilled, editable options — scraped values are proposals to confirm."""
    host = re.sub(r"^https?://(www\.)?", "", source_url or "").split("/")[0]
    options: List[DecisionOption] = []

    for field in VENUE_FIELDS:
        if field not in facts:
            continue
        value = facts[field]
        options.append(DecisionOption(
            key=field,
            label=FIELD_LABELS.get(field, field),
            reasoning=(
                f"Read from {host}: {value!r}. Confirm or correct it — a page "
                f"can be out of date, and a wrong capacity misreads every fit "
                f"badge that follows."
            ),
            data={"value": value, "field": field, "source": host},
        ))

    amenities = _clean_amenities(facts.get("amenities"))
    for key in AMENITIES:
        answer = amenities[key]
        if answer == "yes":
            reasoning = f"{host} states this is available."
        elif answer == "no":
            reasoning = (
                f"{host} states this is NOT available — budget an outside "
                f"vendor, or confirm by phone before ruling the venue out."
            )
        else:
            reasoning = (
                f"Not stated on {host}. Someone has to ask — an unanswered "
                f"question is not a no."
            )
        options.append(DecisionOption(
            key=f"amenity_{key}",
            label=AMENITY_LABELS[key],
            reasoning=reasoning,
            data={"value": answer, "amenity": key, "source": host},
        ))
    return options
