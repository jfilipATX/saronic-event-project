"""T11.5 — playbook composer (RED first).

The playbook is the actual deliverable the human coordinator walks away with:
one document per event that chains every decision into a runnable plan, with
the reasoning and the roads-not-taken preserved.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo
from app.db.models import Decision, DecisionOption, Event, EventVariable
from app.features.playbook import compose_playbook, render_markdown


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from app.db import schema_sql_text as sql

    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def planned_event(conn: sqlite3.Connection) -> int:
    eid = repo.create_event(
        conn,
        Event(name="Saronic Fleet Week", city="Austin", audience_estimate=4200,
              event_type="convention"),
    )
    repo.record_decision(
        conn,
        Decision(
            event_id=eid,
            step="event_type",
            question="What kind of event is this?",
            options=[
                DecisionOption("convention", "Convention", "Widest reach; matches launch goal."),
                DecisionOption("panel", "Panel", "Cheaper but too small for a product launch."),
            ],
            chosen_key="convention",
            decided_by="coordinator",
        ),
    )
    repo.record_decision(
        conn,
        Decision(
            event_id=eid,
            step="audience",
            question="What audience size do we plan for?",
            options=[DecisionOption("4200", "4,200 attendees", "Base 5000 x Austin 1.2 uplift, "
                                                              "discounted for first-year event.")],
            chosen_key="4200",
        ),
    )
    repo.record_decision(
        conn,
        Decision(
            event_id=eid,
            step="venue",
            question="Which venue should host the event?",
            options=[
                DecisionOption("austin-convention-center", "Austin Convention Center",
                               "9000 capacity clears 4200 with headroom.",
                               data={"capacity": 9000}),
                DecisionOption("palmer-events-center", "Palmer Events Center",
                               "3000 capacity is under the estimate.",
                               data={"capacity": 3000}),
            ],
            chosen_key="austin-convention-center",
        ),
    )
    repo.add_variable(conn, EventVariable(event_id=eid, kind="vip", value="DoD delegation",
                                          notes="Needs escort + badging lead time."))
    return eid


class TestComposePlaybook:
    def test_playbook_carries_event_header(self, conn, planned_event):
        pb = compose_playbook(conn, planned_event)
        assert pb.event.name == "Saronic Fleet Week"
        assert pb.event.city == "Austin"

    def test_sections_follow_the_coordinator_workflow_order(self, conn, planned_event):
        pb = compose_playbook(conn, planned_event)
        assert [s.step for s in pb.sections] == ["event_type", "audience", "venue"]

    def test_each_section_shows_choice_reasoning_and_alternatives(self, conn, planned_event):
        pb = compose_playbook(conn, planned_event)
        venue = next(s for s in pb.sections if s.step == "venue")
        assert venue.chosen_label == "Austin Convention Center"
        assert "9000 capacity" in venue.reasoning
        assert [a.label for a in venue.alternatives] == ["Palmer Events Center"]

    def test_variables_are_carried_into_the_playbook(self, conn, planned_event):
        pb = compose_playbook(conn, planned_event)
        assert pb.variables[0].kind == "vip"
        assert "escort" in pb.variables[0].notes

    def test_open_questions_surface_pending_decisions_for_the_human(self, conn, planned_event):
        repo.record_decision(
            conn,
            Decision(
                event_id=planned_event,
                step="slides",
                question="Which deck template?",
                options=[DecisionOption("dark-hero", "Dark hero", "Matches monochrome brand.")],
            ),
        )
        pb = compose_playbook(conn, planned_event)
        assert [q.step for q in pb.open_questions] == ["slides"]
        assert pb.is_complete is False

    def test_playbook_with_no_pending_decisions_is_complete(self, conn, planned_event):
        pb = compose_playbook(conn, planned_event)
        assert pb.open_questions == []
        assert pb.is_complete is True

    def test_unknown_event_raises(self, conn):
        with pytest.raises(LookupError):
            compose_playbook(conn, 9999)


class TestRenderMarkdown:
    def test_markdown_contains_decisions_reasoning_and_alternatives(self, conn, planned_event):
        md = render_markdown(compose_playbook(conn, planned_event))
        assert "# Saronic Fleet Week" in md
        assert "Austin Convention Center" in md
        assert "9000 capacity clears 4200" in md
        # Roads not taken must be visible to the human, not silently dropped.
        assert "Palmer Events Center" in md
        assert "DoD delegation" in md

    def test_markdown_flags_open_questions_section(self, conn, planned_event):
        repo.record_decision(
            conn,
            Decision(
                event_id=planned_event,
                step="checkin",
                question="QR check-in or manual list?",
                options=[DecisionOption("qr", "QR check-in", "Faster at door, needs phones.")],
            ),
        )
        md = render_markdown(compose_playbook(conn, planned_event))
        assert "Open questions" in md
        assert "QR check-in or manual list?" in md
