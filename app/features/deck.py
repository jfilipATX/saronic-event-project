"""T11 — deck composition from the playbook.

The deck is generated *from* the playbook, never assembled independently. That
is the point: a coordinator who changes the venue gets a deck that changes with
it, and there is no path by which the slides can assert something the decision
log does not support.

Slides name an image **role** (T10) rather than a file, so the deck inherits
brand-first resolution: Saronic hardware and lockups always resolve, and a stock
outage costs a background photo rather than the slide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.features.images import ImageResolver
from app.features.playbook import Playbook


@dataclass
class Slide:
    kind: str                      # title | decision | open-questions | closing
    title: str
    body: str = ""
    step: Optional[str] = None
    image_role: Optional[str] = None
    image_url: Optional[str] = None
    notes: str = ""


@dataclass
class Deck:
    event_name: str
    slides: List[Slide] = field(default_factory=list)
    is_draft: bool = False


def _image(resolver: ImageResolver, role: str, city: str = "") -> Optional[str]:
    asset = resolver.resolve(role, city=city)
    return asset.url if asset else None


def build_deck(playbook: Playbook, resolver: ImageResolver) -> Deck:
    """Compose the slide sequence for ``playbook``."""
    ev = playbook.event
    city = ev.city or ""
    slides: List[Slide] = []

    # ── Title ──
    subtitle_bits = [b for b in (ev.city, ev.event_type) if b]
    if ev.audience_estimate:
        subtitle_bits.append(f"{ev.audience_estimate:,} attendees")
    slides.append(Slide(
        kind="title",
        title=ev.name,
        body=" · ".join(subtitle_bits),
        image_role="hero-16x9",
        image_url=_image(resolver, "hero-16x9", city),
        notes="Dark overlay at 60% so title text stays AA over the hero image.",
    ))

    # ── One slide per settled decision ──
    for section in playbook.sections:
        body = section.reasoning
        if section.note:
            body += f"\n\nRevised: {section.note}"
        alt = ", ".join(a.label for a in section.alternatives)
        slides.append(Slide(
            kind="decision",
            title=f"{section.title}: {section.chosen_label}",
            body=body,
            step=section.step,
            image_role="imagery-alt",
            image_url=_image(resolver, "imagery-alt", city),
            notes=f"Also considered: {alt}" if alt else "",
        ))

    # ── Anything still unanswered, stated plainly ──
    if playbook.open_questions:
        lines = []
        for q in playbook.open_questions:
            lines.append(f"{q.title} — {q.question}")
            for o in q.options:
                lines.append(f"  · {o.label}: {o.reasoning}")
        slides.append(Slide(
            kind="open-questions",
            title="Decisions still needed",
            body="\n".join(lines),
            notes="This deck is a draft until these are answered.",
        ))

    # ── Closing ──
    slides.append(Slide(
        kind="closing",
        title="Saronic",
        image_role="logo-on-dark",
        image_url=_image(resolver, "logo-on-dark", city),
    ))

    return Deck(event_name=ev.name, slides=slides, is_draft=not playbook.is_complete)


def render_deck_markdown(deck: Deck) -> str:
    """Portable outline of the deck — the review format before HTML render."""
    out: List[str] = [f"# Deck — {deck.event_name}"]
    if deck.is_draft:
        out += ["", "**DRAFT — open decisions remain.**"]
    out += [""]
    for i, s in enumerate(deck.slides, start=1):
        out += [f"## Slide {i} · {s.kind} — {s.title}", ""]
        if s.body:
            out += [s.body, ""]
        if s.image_role:
            status = s.image_url or "(unavailable — falls back to brand surface)"
            out += [f"`image-role: {s.image_role}` → {status}", ""]
        if s.notes:
            out += [f"> {s.notes}", ""]
    return "\n".join(out).rstrip() + "\n"
