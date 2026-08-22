"""Empty provider slates must become open questions, not mid-chain crashes.

Bug this pins (found by the user): creating an event in a city with no venue
data raised 400 "A decision must offer at least one option" *at the audience
step*, because choosing the audience stages the venue step and the mock provider
returns nothing for unknown cities.

Two things were wrong. The coordinator was punished at the wrong step for a data
gap one layer down, and "we have no options" — which is real, useful information
— was rendered as a failure. A step with no options is a legitimate state: it
tells the coordinator the tool cannot help here and why.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Decision
from app.features.workflow import CoordinatorWorkflow
from app.main import create_app

UNKNOWN_CITY = "San Marcos"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def wf(conn) -> CoordinatorWorkflow:
    return CoordinatorWorkflow(conn)


class TestUnknownCityDoesNotCrashTheChain:
    def test_choosing_audience_in_an_unknown_city_succeeds(self, wf):
        eid = wf.start_event(name="Launch", city=UNKNOWN_CITY)
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")  # must not raise

    def test_venue_stages_as_an_unanswerable_open_question(self, wf):
        eid = wf.start_event(name="Launch", city=UNKNOWN_CITY)
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        pending = wf.pending(eid)
        assert [d.step for d in pending] == ["venue"]
        assert pending[0].options == []
        assert pending[0].is_pending

    def test_the_blocked_step_explains_what_is_missing_and_why(self, wf):
        eid = wf.start_event(name="Launch", city=UNKNOWN_CITY)
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        blocked = wf.pending(eid)[0]
        # The question stays clean ("Which venue...?"); the city and the why
        # live in blocked_reason, which every surface renders alongside it.
        assert blocked.blocked_reason
        assert UNKNOWN_CITY in blocked.blocked_reason

    def test_a_known_city_is_unaffected(self, wf):
        eid = wf.start_event(name="Launch", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        assert len(wf.pending(eid)[0].options) == 3

    def test_choosing_anything_on_a_blocked_step_is_still_rejected(self, wf):
        """No options means no valid answer — the guard must still hold."""
        eid = wf.start_event(name="Launch", city=UNKNOWN_CITY)
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        with pytest.raises(ValueError):
            wf.choose(eid, step="venue", key="anything")


class TestRepositoryStillGuardsRealMistakes:
    """The empty-options guard is relaxed only for explicitly blocked steps."""

    def test_an_unblocked_decision_with_no_options_is_still_rejected(self, conn):
        eid = repo.create_event(conn, __import__("app.db.models", fromlist=["Event"]).Event(
            name="E", city="Austin"))
        with pytest.raises(ValueError, match="at least one option"):
            repo.record_decision(conn, Decision(
                event_id=eid, step="venue", question="Which venue?", options=[]))

    def test_a_blocked_decision_with_no_options_is_allowed(self, conn):
        eid = repo.create_event(conn, __import__("app.db.models", fromlist=["Event"]).Event(
            name="E", city="Nowhere"))
        did = repo.record_decision(conn, Decision(
            event_id=eid, step="venue", question="Which venue?", options=[],
            blocked_reason="No venue data for Nowhere."))
        got = repo.get_decision(conn, did)
        assert got.blocked_reason == "No venue data for Nowhere."
        assert got.is_blocked is True

    def test_a_normal_decision_is_not_blocked(self, conn):
        from app.db.models import DecisionOption, Event

        eid = repo.create_event(conn, Event(name="E", city="Austin"))
        did = repo.record_decision(conn, Decision(
            event_id=eid, step="venue", question="Which venue?",
            options=[DecisionOption("a", "A", "because")]))
        assert repo.get_decision(conn, did).is_blocked is False


class TestPlaybookSurfacesTheBlockedStep:
    def test_blocked_step_appears_as_an_open_question(self, wf, conn):
        from app.features.playbook import compose_playbook

        eid = wf.start_event(name="Launch", city=UNKNOWN_CITY)
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        pb = compose_playbook(conn, eid)
        assert [q.step for q in pb.open_questions] == ["venue"]
        assert pb.is_complete is False

    def test_markdown_export_states_the_blocker(self, wf, conn):
        from app.features.playbook import compose_playbook, render_markdown

        eid = wf.start_event(name="Launch", city=UNKNOWN_CITY)
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        md = render_markdown(compose_playbook(conn, eid))
        assert UNKNOWN_CITY in md
        assert "no venue" in md.lower()


class TestUiRendersTheBlockedStep:
    @pytest.fixture()
    def client(self, tmp_path) -> TestClient:
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    def test_audience_choice_returns_a_redirect_not_a_400(self, client):
        r = client.post("/events", data={"name": "Launch", "city": UNKNOWN_CITY},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/decide",
                    data={"step": "event_type", "key": "convention"})
        r2 = client.post(f"/events/{eid}/decide",
                         data={"step": "audience", "key": "baseline"},
                         follow_redirects=False)
        assert r2.status_code in (302, 303), r2.text

    def test_venue_page_explains_the_gap_and_offers_a_way_out(self, client):
        r = client.post("/events", data={"name": "Launch", "city": UNKNOWN_CITY},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/decide",
                    data={"step": "event_type", "key": "convention"})
        client.post(f"/events/{eid}/decide",
                    data={"step": "audience", "key": "baseline"})
        page = client.get(f"/events/{eid}/steps/venue")
        assert page.status_code == 200
        assert UNKNOWN_CITY in page.text
        assert "pending-note" in page.text          # designer's contract
        assert "btn-quiet" in page.text             # recovery, not happy path

    def test_known_city_venue_page_is_unchanged(self, client):
        r = client.post("/events", data={"name": "Launch", "city": "Austin"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/decide",
                    data={"step": "event_type", "key": "convention"})
        client.post(f"/events/{eid}/decide",
                    data={"step": "audience", "key": "baseline"})
        page = client.get(f"/events/{eid}/steps/venue")
        assert "fit-fits" in page.text
        assert "btn-quiet" not in page.text
