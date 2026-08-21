"""T11.5 — decision persistence (RED first).

The coordinator is a human. Every feature must present *options + reasoning*,
the human chooses, and the choice is recorded immutably so the playbook can
show not just WHAT was decided but WHAT ELSE was on the table and WHY.

These tests pin the contract before the implementation exists.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo
from app.db.models import Decision, DecisionOption, Event


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from app.db import schema_sql_text as sql

    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def event_id(conn: sqlite3.Connection) -> int:
    return repo.create_event(conn, Event(name="Saronic Fleet Week", city="Austin"))


def _venue_options() -> list[DecisionOption]:
    return [
        DecisionOption(
            key="austin-convention-center",
            label="Austin Convention Center",
            reasoning="9000 capacity clears the 4200 estimate with headroom; downtown.",
            data={"capacity": 9000, "rating": 4.6},
        ),
        DecisionOption(
            key="palmer-events-center",
            label="Palmer Events Center",
            reasoning="3000 capacity is under estimate; cheaper, better for a scoped-down event.",
            data={"capacity": 3000, "rating": 4.4},
        ),
    ]


class TestRecordDecision:
    def test_records_choice_with_all_options_and_reasoning(self, conn, event_id):
        d_id = repo.record_decision(
            conn,
            Decision(
                event_id=event_id,
                step="venue",
                question="Which venue should host the event?",
                options=_venue_options(),
                chosen_key="austin-convention-center",
                decided_by="coordinator",
            ),
        )
        got = repo.get_decision(conn, d_id)
        assert got.step == "venue"
        assert got.chosen_key == "austin-convention-center"
        # The rejected option survives — that is the whole point of the log.
        assert {o.key for o in got.options} == {
            "austin-convention-center",
            "palmer-events-center",
        }
        assert got.chosen_option.reasoning.startswith("9000 capacity")
        assert got.decided_at  # stamped server-side

    def test_chosen_key_must_be_one_of_the_offered_options(self, conn, event_id):
        with pytest.raises(ValueError, match="not among the offered options"):
            repo.record_decision(
                conn,
                Decision(
                    event_id=event_id,
                    step="venue",
                    question="Which venue?",
                    options=_venue_options(),
                    chosen_key="some-venue-nobody-offered",
                ),
            )

    def test_pending_decision_may_have_no_choice_yet(self, conn, event_id):
        """A step can be staged for the human before they have decided."""
        d_id = repo.record_decision(
            conn,
            Decision(
                event_id=event_id,
                step="event_type",
                question="What kind of event is this?",
                options=[
                    DecisionOption("panel", "Panel", "Cheapest, tightest audience."),
                    DecisionOption("convention", "Convention", "Widest reach, highest cost."),
                ],
            ),
        )
        got = repo.get_decision(conn, d_id)
        assert got.chosen_key is None
        assert got.chosen_option is None
        assert got.is_pending is True

    def test_options_must_not_be_empty(self, conn, event_id):
        with pytest.raises(ValueError, match="at least one option"):
            repo.record_decision(
                conn,
                Decision(event_id=event_id, step="venue", question="Which venue?", options=[]),
            )


class TestRevisions:
    def test_changing_a_decision_supersedes_rather_than_overwrites(self, conn, event_id):
        first = repo.record_decision(
            conn,
            Decision(
                event_id=event_id,
                step="venue",
                question="Which venue?",
                options=_venue_options(),
                chosen_key="austin-convention-center",
            ),
        )
        second = repo.revise_decision(
            conn, first, chosen_key="palmer-events-center", note="Budget cut in half."
        )

        old = repo.get_decision(conn, first)
        new = repo.get_decision(conn, second)
        assert old.superseded_by == second
        assert old.chosen_key == "austin-convention-center"  # history intact
        assert new.chosen_key == "palmer-events-center"
        assert new.note == "Budget cut in half."
        assert new.superseded_by is None

    def test_current_decisions_returns_only_live_choices_one_per_step(self, conn, event_id):
        first = repo.record_decision(
            conn,
            Decision(
                event_id=event_id,
                step="venue",
                question="Which venue?",
                options=_venue_options(),
                chosen_key="austin-convention-center",
            ),
        )
        repo.revise_decision(conn, first, chosen_key="palmer-events-center")
        repo.record_decision(
            conn,
            Decision(
                event_id=event_id,
                step="audience",
                question="What audience size do we plan for?",
                options=[DecisionOption("4200", "4,200 attendees", "Mock model, Austin uplift.")],
                chosen_key="4200",
            ),
        )

        current = repo.current_decisions(conn, event_id)
        assert [d.step for d in current] == ["venue", "audience"]
        assert current[0].chosen_key == "palmer-events-center"

    def test_full_history_is_append_only(self, conn, event_id):
        first = repo.record_decision(
            conn,
            Decision(
                event_id=event_id,
                step="venue",
                question="Which venue?",
                options=_venue_options(),
                chosen_key="austin-convention-center",
            ),
        )
        repo.revise_decision(conn, first, chosen_key="palmer-events-center")
        history = repo.decision_history(conn, event_id)
        assert len(history) == 2
        assert history[0].id == first  # oldest first
