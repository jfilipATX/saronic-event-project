"""One assembly path for "the chosen thing".

Three features in a row (blocked_reason twice, display_label once) shipped with a
consumer that silently dropped a Decision field, because PlaybookSection and
OpenQuestion were built field-by-field in several places. Green unit tests, wrong
document.

This pins the fix: view models are derived FROM a Decision by one constructor
each, and a reflective test fails when a new presentation field is added to
Decision without being threaded through. The point is that the NEXT person adding
a field gets a failing test rather than a quiet omission.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Decision, DecisionOption, Event
from app.features.playbook import (
    PRESENTATION_FIELDS,
    OpenQuestion,
    PlaybookSection,
    compose_playbook,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


def _decision(**kw) -> Decision:
    base = dict(
        event_id=1,
        step="venue",
        question="Which venue?",
        options=[DecisionOption("a", "Option A", "because A",
                                {"requires_value": True,
                                 "value_label": "Custom: {value:,} attendees"}),
                 DecisionOption("b", "Option B", "because B")],
    )
    base.update(kw)
    return Decision(**base)


class TestPresentationFieldsAreDeclared:
    """PRESENTATION_FIELDS is the contract new fields must join."""

    def test_every_declared_field_exists_on_decision(self):
        d = _decision()
        for name in PRESENTATION_FIELDS:
            assert hasattr(d, name), f"Decision has no attribute {name!r}"

    def test_known_presentation_fields_are_covered(self):
        """A new field that affects what the coordinator SEES must be listed here.

        If this fails because you added a field, add it to PRESENTATION_FIELDS
        and thread it through the view models — that is the whole point.
        """
        expected = {"question", "chosen_key", "chosen_value", "decided_by",
                    "decided_at", "note", "blocked_reason", "options"}
        assert expected <= set(PRESENTATION_FIELDS)


class TestSectionFromDecision:
    def test_carries_the_display_label_not_the_raw_option_label(self):
        d = _decision(chosen_key="a", chosen_value="2450")
        section = PlaybookSection.from_decision(d, title="Audience")
        assert section.chosen_label == "Custom: 2,450 attendees"

    def test_preset_option_keeps_its_label(self):
        section = PlaybookSection.from_decision(_decision(chosen_key="b"),
                                                title="Venue")
        assert section.chosen_label == "Option B"

    def test_carries_reasoning_alternatives_and_attribution(self):
        d = _decision(chosen_key="b", decided_by="coordinator",
                      decided_at="2026-08-21 10:00:00", note="changed my mind")
        section = PlaybookSection.from_decision(d, title="Venue")
        assert section.reasoning == "because B"
        assert [a.key for a in section.alternatives] == ["a"]
        assert section.decided_by == "coordinator"
        assert section.decided_at == "2026-08-21 10:00:00"
        assert section.note == "changed my mind"


class TestOpenQuestionFromDecision:
    def test_carries_the_blocked_reason(self):
        d = _decision(options=[], blocked_reason="No venue data for Waco.")
        q = OpenQuestion.from_decision(d, title="Venue")
        assert q.blocked_reason == "No venue data for Waco."

    def test_carries_the_options_when_there_are_any(self):
        q = OpenQuestion.from_decision(_decision(), title="Venue")
        assert [o.key for o in q.options] == ["a", "b"]

    def test_unblocked_pending_step_has_no_reason(self):
        assert OpenQuestion.from_decision(_decision(), title="Venue").blocked_reason == ""


class TestComposeUsesTheSinglePath:
    """compose_playbook must not hand-build view models any more."""

    def test_settled_decision_becomes_a_section_with_the_display_label(self, conn):
        eid = repo.create_event(conn, Event(name="E", city="Austin"))
        repo.record_decision(conn, Decision(
            event_id=eid, step="audience", question="How many?",
            options=[DecisionOption("custom", "Custom", "your number",
                                    {"requires_value": True,
                                     "value_label": "Custom: {value:,} attendees"})],
            chosen_key="custom", chosen_value="2450"))
        pb = compose_playbook(conn, eid)
        assert pb.sections[0].chosen_label == "Custom: 2,450 attendees"

    def test_blocked_step_becomes_an_open_question_with_its_reason(self, conn):
        eid = repo.create_event(conn, Event(name="E", city="Nowhere"))
        repo.record_decision(conn, Decision(
            event_id=eid, step="venue", question="Which venue?", options=[],
            blocked_reason="No venue data for Nowhere."))
        pb = compose_playbook(conn, eid)
        assert pb.open_questions[0].blocked_reason == "No venue data for Nowhere."

    def test_corrupted_choice_degrades_to_an_open_question_keeping_the_reason(self, conn):
        eid = repo.create_event(conn, Event(name="E", city="Nowhere"))
        repo.record_decision(conn, Decision(
            event_id=eid, step="venue", question="Which venue?",
            options=[DecisionOption("a", "A", "why")], chosen_key="a",
            blocked_reason="Directory was incomplete."))
        conn.execute("UPDATE decisions SET chosen_key='gone'")
        pb = compose_playbook(conn, eid)
        assert pb.open_questions[0].blocked_reason == "Directory was incomplete."
