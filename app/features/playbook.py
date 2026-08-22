"""T11.5 — Playbook composer.

The playbook is what the human event coordinator actually walks away with: one
document per event that chains every decision into a runnable plan.

Design rules this module enforces:

1. **Never invent a decision.** The composer only reads what the coordinator
   actually chose. A step with no choice yet becomes an *open question*, not a
   guess.
2. **Show the road not taken.** Every section carries the alternatives and their
   reasoning, so the coordinator (or their boss) can audit the trade-off months
   later.
3. **Ordering follows the workflow, not the clock.** Steps render in the order a
   coordinator works through them; unknown//custom steps sort after the known
   ones, preserving insertion order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.db import repository as repo
from app.db.models import Decision, DecisionOption, Event, EventVariable

#: Canonical order of the coordinator's workflow. Anything not listed here keeps
#: its insertion order and renders after these.
STEP_ORDER: tuple[str, ...] = (
    "event_type",
    "audience",
    "venue",
    "variables",
    "slides",
    "checkin",
)

#: Human-readable section titles. Falls back to a prettified step key.
STEP_TITLES = {
    "event_type": "Event type",
    "audience": "Audience estimate",
    "venue": "Venue",
    "variables": "Event variables",
    "slides": "Presentation",
    "checkin": "Check-in plan",
}


def _title_for(step: str) -> str:
    return STEP_TITLES.get(step, step.replace("_", " ").capitalize())


@dataclass
class PlaybookSection:
    """One settled decision, rendered for the human."""

    step: str
    title: str
    question: str
    chosen_key: str
    chosen_label: str
    reasoning: str
    alternatives: List[DecisionOption] = field(default_factory=list)
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    note: Optional[str] = None


@dataclass
class OpenQuestion:
    """A decision staged for the coordinator but not yet made."""

    step: str
    title: str
    question: str
    options: List[DecisionOption] = field(default_factory=list)
    blocked_reason: str = ""


@dataclass
class Playbook:
    event: Event
    sections: List[PlaybookSection] = field(default_factory=list)
    open_questions: List[OpenQuestion] = field(default_factory=list)
    variables: List[EventVariable] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """True when nothing is left for the human to decide."""
        return not self.open_questions


def _sort_key(decision: Decision, insertion_index: int) -> tuple[int, int]:
    try:
        return (STEP_ORDER.index(decision.step), insertion_index)
    except ValueError:
        return (len(STEP_ORDER), insertion_index)


def compose_playbook(conn, event_id: int) -> Playbook:
    """Assemble the full playbook for ``event_id`` from persisted decisions."""
    event = repo.get_event(conn, event_id)
    if event is None:
        raise LookupError(f"No event with id {event_id}")

    decisions = repo.current_decisions(conn, event_id)
    ordered = sorted(
        decisions, key=lambda d: _sort_key(d, decisions.index(d))
    )

    sections: List[PlaybookSection] = []
    open_questions: List[OpenQuestion] = []
    for d in ordered:
        if d.is_pending:
            open_questions.append(
                OpenQuestion(
                    step=d.step,
                    title=_title_for(d.step),
                    question=d.question,
                    options=d.options,
                    blocked_reason=getattr(d, "blocked_reason", "") or "",
                )
            )
            continue
        chosen = d.chosen_option
        # Defensive: repository validation guarantees this, but a hand-edited DB
        # should degrade to a visible gap rather than a crash.
        if chosen is None:
            open_questions.append(
                OpenQuestion(step=d.step, title=_title_for(d.step),
                             question=d.question, options=d.options,
                             blocked_reason=getattr(d, "blocked_reason", "") or "")
            )
            continue
        sections.append(
            PlaybookSection(
                step=d.step,
                title=_title_for(d.step),
                question=d.question,
                chosen_key=chosen.key,
                chosen_label=d.display_label or chosen.label,
                reasoning=chosen.reasoning,
                alternatives=d.alternatives,
                decided_by=d.decided_by,
                decided_at=d.decided_at,
                note=d.note,
            )
        )

    return Playbook(
        event=event,
        sections=sections,
        open_questions=open_questions,
        variables=repo.list_variables(conn, event_id),
    )


def render_markdown(playbook: Playbook) -> str:
    """Render the playbook as a portable markdown document.

    Markdown (not HTML) is the export primitive on purpose: the coordinator can
    paste it into email, Notion, or a doc without carrying our CSS. The styled
    HTML/PDF export layers on top of this via the designer's playbook tokens.
    """
    ev = playbook.event
    out: List[str] = [f"# {ev.name}", ""]

    meta = []
    if ev.city:
        meta.append(f"**City:** {ev.city}")
    if ev.event_type:
        meta.append(f"**Type:** {ev.event_type}")
    if ev.audience_estimate:
        meta.append(f"**Audience estimate:** {ev.audience_estimate:,}")
    if meta:
        out += [" · ".join(meta), ""]

    status = "Complete" if playbook.is_complete else (
        f"{len(playbook.open_questions)} open question(s) remaining"
    )
    out += [f"**Status:** {status}", "", "---", ""]

    out += ["## Decisions", ""]
    if not playbook.sections:
        out += ["_No decisions recorded yet._", ""]
    for i, s in enumerate(playbook.sections, start=1):
        out += [f"### {i}. {s.title}: {s.chosen_label}", ""]
        out += [f"_{s.question}_", ""]
        if s.reasoning:
            out += [f"**Why:** {s.reasoning}", ""]
        if s.note:
            out += [f"**Note:** {s.note}", ""]
        attribution = " · ".join(
            p for p in (
                f"Decided by {s.decided_by}" if s.decided_by else "",
                f"on {s.decided_at}" if s.decided_at else "",
            ) if p
        )
        if attribution:
            out += [f"<small>{attribution}</small>", ""]
        if s.alternatives:
            out += ["**Also considered:**", ""]
            for alt in s.alternatives:
                reason = f" — {alt.reasoning}" if alt.reasoning else ""
                out += [f"- {alt.label}{reason}"]
            out += [""]

    if playbook.variables:
        out += ["## Event variables", ""]
        for v in playbook.variables:
            note = f" — {v.notes}" if v.notes else ""
            out += [f"- **{v.kind}:** {v.value}{note}"]
        out += [""]

    if playbook.open_questions:
        out += ["## Open questions", "",
                "These need a decision from the coordinator before the plan is runnable.",
                ""]
        for q in playbook.open_questions:
            out += [f"### {q.title}", "", f"{q.question}", ""]
            if q.blocked_reason:
                out += [f"> Blocked: {q.blocked_reason}", ""]
            for o in q.options:
                reason = f" — {o.reasoning}" if o.reasoning else ""
                out += [f"- **{o.label}**{reason}"]
            out += [""]

    return "\n".join(out).rstrip() + "\n"
