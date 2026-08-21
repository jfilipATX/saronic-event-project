"""T6 — the coordinator workflow: create an event and walk the decision chain.

This is the orchestration layer the UI (and later the API) drives. It owns one
rule: **stage options, never choose.** Each ``stage_*`` call records a *pending*
decision carrying the full slate and reasoning; the human answers via ``choose``.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.features.workflow import CoordinatorWorkflow


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def wf(conn) -> CoordinatorWorkflow:
    return CoordinatorWorkflow(conn)


class TestStartEvent:
    def test_creates_event_and_stages_the_first_question(self, wf, conn):
        eid = wf.start_event(name="Saronic Fleet Week", city="Austin")
        event = repo.get_event(conn, eid)
        assert event.name == "Saronic Fleet Week"
        pending = wf.pending(eid)
        assert [d.step for d in pending] == ["event_type"]

    def test_staged_question_is_pending_not_decided(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        assert all(d.is_pending for d in wf.pending(eid))

    def test_staged_options_carry_reasoning(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        opts = wf.pending(eid)[0].options
        assert opts and all(o.reasoning.strip() for o in opts)


class TestChooseAdvancesTheChain:
    def test_choosing_event_type_stages_audience_with_a_bracketed_slate(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention", by="coordinator")
        pending = wf.pending(eid)
        assert [d.step for d in pending] == ["audience"]
        assert len(pending[0].options) >= 3

    def test_choosing_audience_stages_venue_with_the_full_slate(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="conservative")
        pending = wf.pending(eid)
        assert [d.step for d in pending] == ["venue"]
        # All three Austin venues, none hidden.
        assert len(pending[0].options) == 3

    def test_venue_options_carry_fit_badges(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        opts = wf.pending(eid)[0].options
        assert all("fit" in o.data for o in opts)

    def test_choice_is_persisted_on_the_event_row(self, wf, conn):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        event = repo.get_event(conn, eid)
        assert event.event_type == "convention"
        assert event.audience_estimate == 6000  # 5000 base x 1.2 Austin uplift

    def test_choosing_an_unoffered_key_is_rejected(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        with pytest.raises(ValueError, match="not among the offered options"):
            wf.choose(eid, step="event_type", key="grand-ball")

    def test_choosing_a_step_that_was_never_staged_is_rejected(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        with pytest.raises(LookupError, match="venue"):
            wf.choose(eid, step="venue", key="whatever")

    def test_completing_the_chain_leaves_no_pending_steps(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="conservative")
        wf.choose(eid, step="venue", key="austin-convention-center")
        assert wf.pending(eid) == []


class TestRevisionRestagesDownstream:
    def test_revising_audience_restages_venue_with_new_fit(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        wf.choose(eid, step="venue", key="austin-convention-center")

        wf.revise(eid, step="audience", key="conservative",
                  note="Budget cut in half.")
        # Venue must be re-offered: fit verdicts changed under it.
        assert [d.step for d in wf.pending(eid)] == ["venue"]

    def test_revision_preserves_history(self, wf, conn):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        wf.revise(eid, step="audience", key="conservative", note="Budget cut.")
        history = repo.decision_history(conn, eid)
        keys = [d.chosen_key for d in history if d.step == "audience"]
        assert "baseline" in keys and "conservative" in keys

    def test_withdrawn_downstream_answer_leaves_the_audit_trail_intact(self, wf, conn):
        """Invalidated answers are withdrawn, not deleted — the coordinator must
        still be able to see that a venue had been chosen before the budget cut."""
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        wf.choose(eid, step="venue", key="austin-convention-center")
        wf.revise(eid, step="audience", key="conservative", note="Budget cut.")

        history = repo.decision_history(conn, eid)
        withdrawn = [d for d in history
                     if d.step == "venue" and d.chosen_key == "austin-convention-center"]
        assert withdrawn, "the superseded venue choice must survive in history"
        # ...but it is no longer a live decision.
        live_steps = [d.step for d in repo.current_decisions(conn, eid)]
        assert live_steps.count("venue") == 1
        assert wf.pending(eid)[0].step == "venue"

    def test_revising_event_type_invalidates_both_later_steps(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        wf.choose(eid, step="venue", key="austin-convention-center")
        wf.revise(eid, step="event_type", key="panel", note="Scope change.")
        # Audience must be re-asked; venue must not survive on a stale premise.
        assert [d.step for d in wf.pending(eid)] == ["audience"]


class TestWorkflowFeedsThePlaybook:
    def test_completed_workflow_produces_a_complete_playbook(self, wf):
        from app.features.playbook import compose_playbook

        eid = wf.start_event(name="Saronic Fleet Week", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="conservative")
        wf.choose(eid, step="venue", key="austin-convention-center")

        pb = compose_playbook(wf.conn, eid)
        assert pb.is_complete
        assert [s.step for s in pb.sections] == ["event_type", "audience", "venue"]

    def test_partial_workflow_surfaces_the_next_question(self, wf):
        from app.features.playbook import compose_playbook

        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        pb = compose_playbook(wf.conn, eid)
        assert pb.is_complete is False
        assert [q.step for q in pb.open_questions] == ["audience"]
