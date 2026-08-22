"""P4-2 — venue opt-out: Saronic has a booth at someone else's event.

The venue step assumes we are choosing a venue. Often we are not: the venue is
already established because we booked a booth at an existing event. That is not
a missing answer, and it is not a blocked step either.

The distinction that drives the design:

* **blocked** — we looked and found nothing. An open question the playbook keeps
  nagging about, because someone still has to solve it.
* **resolved externally** — there is nothing to solve. The venue is decided, by
  someone else, and the chain should proceed with that as a recorded fact.

Treating the second as the first would leave every booth event permanently
showing an open question it can never close.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo, schema_sql_text as sql
from app.features.playbook import compose_playbook
from app.features.workflow import CoordinatorWorkflow
from app.main import create_app


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def staged(conn) -> tuple:
    wf = CoordinatorWorkflow(conn)
    eid = wf.start_event(name="Fleet Week", city="Austin")
    wf.choose(eid, "event_type", "convention")
    wf.choose(eid, "audience", "baseline")
    return wf, eid


class TestOptOut:
    def test_opting_out_answers_the_venue_step(self, staged):
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        decision = next(d for d in repo.current_decisions(wf.conn, eid)
                        if d.step == "venue")
        assert decision.chosen_key
        assert decision.is_pending is False

    def test_the_host_event_is_recorded(self, staged):
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        decision = next(d for d in repo.current_decisions(wf.conn, eid)
                        if d.step == "venue")
        assert "Sea-Air-Space 2026" in (decision.display_label or "")

    def test_it_is_not_a_blocked_decision(self, staged):
        """Blocked means unsolved. This is solved, elsewhere."""
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        decision = next(d for d in repo.current_decisions(wf.conn, eid)
                        if d.step == "venue")
        assert decision.is_blocked is False
        assert decision.blocked_reason is None

    def test_the_playbook_has_no_open_question(self, staged):
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        playbook = compose_playbook(wf.conn, eid)
        assert not [q for q in playbook.open_questions if q.step == "venue"]

    def test_the_playbook_states_the_host_event(self, staged):
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        playbook = compose_playbook(wf.conn, eid)
        section = next(s for s in playbook.sections if s.step == "venue")
        assert "Sea-Air-Space 2026" in section.chosen_label

    def test_the_reasoning_explains_why_no_venue_was_chosen(self, staged):
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        decision = next(d for d in repo.current_decisions(wf.conn, eid)
                        if d.step == "venue")
        option = decision.chosen_option
        assert "booth" in option.reasoning.lower()

    def test_a_host_event_name_is_required(self, staged):
        """'Someone else's venue' is only useful if we say whose."""
        wf, eid = staged
        with pytest.raises(ValueError, match="which event"):
            wf.opt_out_of_venue(eid, host_event="   ")

    def test_opting_out_is_revisable(self, staged):
        """A booth booking can fall through — the chain must recover."""
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        wf.revise(eid, "venue", "austin-convention-center")
        decision = next(d for d in repo.current_decisions(wf.conn, eid)
                        if d.step == "venue")
        assert decision.chosen_key == "austin-convention-center"

    def test_the_opt_out_stays_in_the_history(self, staged):
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        wf.revise(eid, "venue", "austin-convention-center")
        history = repo.decision_history(wf.conn, eid)
        assert any("Sea-Air-Space" in (d.display_label or "") for d in history)

    def test_downstream_steps_still_stage(self, staged):
        """A booth event still needs slides, invites and a run of show."""
        wf, eid = staged
        wf.opt_out_of_venue(eid, host_event="Sea-Air-Space 2026")
        playbook = compose_playbook(wf.conn, eid)
        assert playbook.sections


class TestOptOutInTheUi:
    @pytest.fixture()
    def client(self, tmp_path) -> TestClient:
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    @pytest.fixture()
    def eid(self, client) -> int:
        r = client.post("/events", data={"name": "Fleet Week", "city": "Austin"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        for step, key in (("event_type", "convention"), ("audience", "baseline")):
            client.post(f"/events/{eid}/decide", data={"step": step, "key": key})
        return eid

    def test_the_venue_step_offers_opting_out(self, client, eid):
        assert "opt-out" in client.get(f"/events/{eid}/steps/venue").text.lower()

    def test_opting_out_records_the_host_event(self, client, eid):
        client.post(f"/events/{eid}/venues/opt-out",
                    data={"host_event": "Sea-Air-Space 2026"},
                    follow_redirects=False)
        assert "Sea-Air-Space 2026" in client.get(f"/events/{eid}/playbook").text

    def test_a_blank_host_event_is_refused(self, client, eid):
        r = client.post(f"/events/{eid}/venues/opt-out", data={"host_event": " "})
        assert r.status_code == 400
        assert "which event" in r.text.lower()

    def test_the_chain_continues_after_opting_out(self, client, eid):
        client.post(f"/events/{eid}/venues/opt-out",
                    data={"host_event": "Sea-Air-Space 2026"},
                    follow_redirects=False)
        assert client.get(f"/events/{eid}/slides").status_code == 200
        assert client.get(f"/events/{eid}/playbook").status_code == 200

    def test_the_playbook_shows_no_open_venue_question(self, client, eid):
        client.post(f"/events/{eid}/venues/opt-out",
                    data={"host_event": "Sea-Air-Space 2026"},
                    follow_redirects=False)
        # Assert on the rendered BODY, not the whole document: the template's
        # own HTML comment contains the phrase "open questions" and made a
        # naive substring check meaningless.
        import re

        page = client.get(f"/events/{eid}/playbook").text
        body = re.sub(r"<!--.*?-->", "", page, flags=re.S)
        assert "open question" not in body.lower()
        assert "Sea-Air-Space 2026" in body
