"""T6 — coordinator workflow orchestration.

The decision chain: **event type → audience → venue**, each step staged as a
pending decision carrying its full option slate and reasoning, each answered by
the human.

Two invariants this class exists to protect:

1. **Stage, never choose.** ``stage_*`` writes a pending decision. Only
   ``choose``/``revise`` set a ``chosen_key``, and only with a key the human
   passed in. The tool cannot advance the plan on its own.
2. **Revision invalidates downstream.** Changing the audience changes which
   venues fit, so the venue question is re-staged rather than left showing a
   verdict computed against a number that no longer applies. Silent staleness is
   the failure mode that would quietly mislead a coordinator.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from app.db import repository as repo
from app.db.models import Decision, Event
from app.features.audience import build_audience_options
from app.features.event_type import build_event_type_options
from app.features.venue_options import build_venue_options
from app.providers.registry import get_audience_provider, get_venue_provider

#: Order of the chain; each step's options depend on the answers before it.
CHAIN: tuple[str, ...] = ("event_type", "audience", "venue")


class CoordinatorWorkflow:
    """Drives one event's decision chain against the persisted decision log."""

    def __init__(self, conn: sqlite3.Connection, config=None) -> None:
        self.conn = conn
        self._config = config
        self._venues = get_venue_provider(config)
        self._audience = get_audience_provider(config)

    # ── queries ──────────────────────────────────────────────────────────────

    def pending(self, event_id: int) -> List[Decision]:
        return [d for d in repo.current_decisions(self.conn, event_id) if d.is_pending]

    def _live(self, event_id: int, step: str) -> Optional[Decision]:
        return next(
            (d for d in repo.current_decisions(self.conn, event_id) if d.step == step),
            None,
        )

    def _answer(self, event_id: int, step: str) -> Optional[str]:
        d = self._live(event_id, step)
        return d.chosen_key if d and not d.is_pending else None

    # ── staging ──────────────────────────────────────────────────────────────

    def start_event(self, name: str, city: str) -> int:
        event_id = repo.create_event(self.conn, Event(name=name, city=city))
        self._stage_event_type(event_id, city)
        return event_id

    def _stage_event_type(self, event_id: int, city: str) -> None:
        repo.record_decision(self.conn, Decision(
            event_id=event_id,
            step="event_type",
            question="What kind of event is this?",
            options=build_event_type_options(city=city),
        ))

    def _stage_audience(self, event_id: int) -> None:
        event = repo.get_event(self.conn, event_id)
        event_type = self._answer(event_id, "event_type") or "other"
        base = self._audience.base_audience(event.city or "", event_type)
        venues = self._venues.search(event.city or "", base)
        repo.record_decision(self.conn, Decision(
            event_id=event_id,
            step="audience",
            question="What audience size should we plan for?",
            options=build_audience_options(
                base=base, city=event.city or "", event_type=event_type, venues=venues,
            ),
        ))

    def _stage_venue(self, event_id: int) -> None:
        event = repo.get_event(self.conn, event_id)
        audience = event.audience_estimate or 0
        city = event.city or ""
        venues = self._venues.search(city, audience)
        options = build_venue_options(venues, audience)
        blocked = None
        if not options:
            # The provider has nothing for this city. That is real information the
            # coordinator needs, not an error: stage the step as answered-by-nobody
            # so the chain continues and the gap is visible where it belongs.
            blocked = (
                f"No venue data for {city or 'this city'}. "
                "The venue directory does not cover it yet — revise the city to one "
                "that is covered, or add venue data for it."
            )
        repo.record_decision(self.conn, Decision(
            event_id=event_id,
            step="venue",
            question="Which venue should host the event?",
            options=options,
            blocked_reason=blocked,
        ))

    _STAGERS = {"audience": "_stage_audience", "venue": "_stage_venue"}

    def _stage_next_after(self, event_id: int, step: str) -> None:
        idx = CHAIN.index(step)
        if idx + 1 >= len(CHAIN):
            return
        nxt = CHAIN[idx + 1]
        if self._live(event_id, nxt) is None:
            getattr(self, self._STAGERS[nxt])(event_id)

    # ── the human's move ─────────────────────────────────────────────────────

    def _validate_value(self, decision: Decision, key: str,
                        value: Optional[str]) -> Optional[str]:
        """Check a coordinator-supplied value against the option that asked for it.

        Rejecting here rather than at the database keeps the error close to the
        human who typed it, and keeps invalid numbers out of the decision log
        entirely rather than stored-then-ignored.
        """
        option = next((o for o in decision.options if o.key == key), None)
        if option is None or not option.data.get("requires_value"):
            return None
        if value is None or not str(value).strip():
            raise ValueError(
                f"The {option.label!r} option requires a value — enter a number."
            )
        raw = str(value).strip().replace(",", "")
        if not raw.isdigit() or int(raw) <= 0:
            raise ValueError(
                f"{value!r} is not a whole number of attendees above zero."
            )
        return str(int(raw))

    def choose(
        self, event_id: int, step: str, key: str, by: str = "coordinator",
        value: Optional[str] = None,
    ) -> int:
        """Record the coordinator's answer to a staged step and advance the chain."""
        decision = self._live(event_id, step)
        if decision is None:
            raise LookupError(
                f"Step {step!r} has not been staged for event {event_id}; "
                "answer the earlier steps first."
            )
        if not decision.is_pending:
            return self.revise(event_id, step=step, key=key, by=by, value=value)

        resolved = self._validate_value(decision, key, value)

        # Re-record with the choice. record_decision() rejects unoffered keys.
        decision.chosen_key = key
        decision.chosen_value = resolved
        decision.decided_by = by
        new_id = repo.record_decision(self.conn, decision)
        self.conn.execute(
            "UPDATE decisions SET superseded_by=? WHERE id=?", (new_id, decision.id)
        )
        self._apply_to_event(event_id, step, repo.get_decision(self.conn, new_id))
        self._stage_next_after(event_id, step)
        return new_id

    def revise(
        self,
        event_id: int,
        step: str,
        key: str,
        note: Optional[str] = None,
        by: str = "coordinator",
        value: Optional[str] = None,
    ) -> int:
        """Change an answered step, then re-stage everything downstream of it."""
        decision = self._live(event_id, step)
        if decision is None:
            raise LookupError(f"Step {step!r} has not been staged for event {event_id}.")
        resolved = self._validate_value(decision, key, value)
        new_id = repo.revise_decision(
            self.conn, decision.id, chosen_key=key, note=note, decided_by=by,
            chosen_value=resolved,
        )
        revised = repo.get_decision(self.conn, new_id)
        self._apply_to_event(event_id, step, revised)
        self._invalidate_downstream(event_id, step)
        self._stage_next_after(event_id, step)
        return new_id

    def _invalidate_downstream(self, event_id: int, step: str) -> None:
        """Drop later answers: they were computed against a premise that changed."""
        for later in CHAIN[CHAIN.index(step) + 1:]:
            live = self._live(event_id, later)
            if live is not None:
                self.conn.execute(
                    "UPDATE decisions SET superseded_by=id WHERE id=?", (live.id,)
                )

    def _apply_to_event(self, event_id: int, step: str, decision: Decision) -> None:
        """Denormalise the chosen value onto the event row for cheap reads."""
        chosen = decision.chosen_option
        if chosen is None:
            return
        if step == "event_type":
            self.conn.execute(
                "UPDATE events SET event_type=? WHERE id=?", (chosen.key, event_id)
            )
        elif step == "audience":
            number = chosen.data.get("audience")
            if chosen.data.get("requires_value") and decision.chosen_value:
                number = int(decision.chosen_value)
            self.conn.execute(
                "UPDATE events SET audience_estimate=? WHERE id=?",
                (number, event_id),
            )
