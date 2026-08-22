"""P2-2 — coordinator-supplied audience number.

The guard collision this resolves: ``record_decision`` rejects any chosen_key
that was not offered, which is what makes "the tool never records a choice it
did not offer" structurally true. A free-form number is by definition not
offered.

Resolution (option 1): the slate offers a ``custom`` option whose *value* the
coordinator supplies. The guard stays intact — ``custom`` was genuinely offered —
and the number rides in ``chosen_value``. The audit trail must read identically
whether the number was preset or supplied.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Decision, DecisionOption, Event
from app.features.audience import CUSTOM_KEY, build_audience_options
from app.features.workflow import CoordinatorWorkflow
from app.main import create_app


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def wf(conn) -> CoordinatorWorkflow:
    return CoordinatorWorkflow(conn)


class TestCustomOptionIsOffered:
    def test_slate_includes_a_custom_option(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        assert CUSTOM_KEY in {o.key for o in opts}

    def test_custom_option_carries_reasoning_like_every_other(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        custom = next(o for o in opts if o.key == CUSTOM_KEY)
        assert custom.reasoning.strip()
        assert "venue fit" in custom.reasoning.lower()

    def test_custom_option_is_marked_as_requiring_a_value(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        custom = next(o for o in opts if o.key == CUSTOM_KEY)
        assert custom.data.get("requires_value") is True

    def test_custom_option_sorts_last(self):
        opts = build_audience_options(base=5000, city="Austin", event_type="convention")
        assert opts[-1].key == CUSTOM_KEY

    def test_custom_option_carries_no_sanity_verdict_without_a_number(self):
        from app.providers.base import Venue

        venues = [Venue("Hall", "Austin", 9000, 4.5, "")]
        opts = build_audience_options(base=5000, city="Austin",
                                      event_type="convention", venues=venues)
        custom = next(o for o in opts if o.key == CUSTOM_KEY)
        assert custom.data.get("sanity") is None


class TestChoosingCustomPersistsTheValue:
    def test_choosing_custom_with_a_value_is_recorded(self, wf, conn):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key=CUSTOM_KEY, value="2450")
        d = next(d for d in repo.current_decisions(conn, eid) if d.step == "audience")
        assert d.chosen_key == CUSTOM_KEY
        assert d.chosen_value == "2450"

    def test_the_event_row_gets_the_custom_number(self, wf, conn):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key=CUSTOM_KEY, value="2450")
        assert repo.get_event(conn, eid).audience_estimate == 2450

    def test_venue_fit_recalculates_against_the_custom_number(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key=CUSTOM_KEY, value="2450")
        venue_opts = wf.pending(eid)[0].options
        palmer = next(o for o in venue_opts if o.label == "Palmer Events Center")
        assert palmer.data["fit"] == "fits"          # 3000 >= 2450
        fairmont = next(o for o in venue_opts if o.label == "Fairmont Austin")
        assert fairmont.data["fit"] == "under"       # 1800 < 2450

    def test_choosing_custom_without_a_value_is_rejected(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        with pytest.raises(ValueError, match="requires a value"):
            wf.choose(eid, step="audience", key=CUSTOM_KEY)

    @pytest.mark.parametrize("bad", ["", "   ", "abc", "-5", "0", "1.5", "12e9"])
    def test_a_non_positive_integer_is_rejected(self, wf, bad):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        with pytest.raises(ValueError):
            wf.choose(eid, step="audience", key=CUSTOM_KEY, value=bad)

    def test_a_preset_option_still_ignores_any_stray_value(self, wf, conn):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline", value="999")
        d = next(d for d in repo.current_decisions(conn, eid) if d.step == "audience")
        assert d.chosen_value is None
        assert repo.get_event(conn, eid).audience_estimate == 6000


class TestAuditTrailReadsIdentically:
    def test_chosen_option_label_shows_the_supplied_number(self, wf, conn):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key=CUSTOM_KEY, value="2450")
        d = next(d for d in repo.current_decisions(conn, eid) if d.step == "audience")
        assert d.display_label == "Custom: 2,450 attendees"

    def test_preset_display_label_is_unchanged(self, wf, conn):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        d = next(d for d in repo.current_decisions(conn, eid) if d.step == "audience")
        assert d.display_label == "Modelled estimate: 6,000 attendees"

    def test_playbook_renders_the_custom_number(self, wf, conn):
        from app.features.playbook import compose_playbook, render_markdown

        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key=CUSTOM_KEY, value="2450")
        md = render_markdown(compose_playbook(conn, eid))
        assert "2,450" in md

    def test_revision_to_custom_preserves_history(self, wf, conn):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        wf.revise(eid, step="audience", key=CUSTOM_KEY, value="2450",
                  note="Confirmed headcount from the invite list.")
        history = repo.decision_history(conn, eid)
        # The originally-staged (undecided) row is in history too; the audit
        # trail we care about is the sequence of actual answers.
        audience = [d for d in history if d.step == "audience" and not d.is_pending]
        assert [d.chosen_key for d in audience] == ["baseline", CUSTOM_KEY]
        assert audience[-1].chosen_value == "2450"


class TestGuardStillHolds:
    def test_an_unoffered_key_is_still_rejected(self, wf):
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        with pytest.raises(ValueError, match="not among the offered options"):
            wf.choose(eid, step="audience", key="made-up", value="2450")

    def test_a_value_on_an_option_that_does_not_take_one_is_ignored(self, conn):
        eid = repo.create_event(conn, Event(name="E", city="Austin"))
        did = repo.record_decision(conn, Decision(
            event_id=eid, step="audience", question="How many?",
            options=[DecisionOption("baseline", "Baseline", "modelled")],
            chosen_key="baseline", chosen_value="999"))
        assert repo.get_decision(conn, did).chosen_value is None


class TestUiCustomAudience:
    @pytest.fixture()
    def client(self, tmp_path) -> TestClient:
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    def _event(self, client) -> int:
        r = client.post("/events", data={"name": "E", "city": "Austin"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/decide",
                    data={"step": "event_type", "key": "convention"})
        return eid

    def test_audience_page_offers_a_custom_input(self, client):
        eid = self._event(client)
        page = client.get(f"/events/{eid}/steps/audience")
        assert 'name="value"' in page.text

    def test_posting_a_custom_value_advances_the_chain(self, client):
        eid = self._event(client)
        r = client.post(f"/events/{eid}/decide",
                        data={"step": "audience", "key": CUSTOM_KEY, "value": "2450"},
                        follow_redirects=False)
        assert r.status_code in (302, 303), r.text
        assert "2,450" in client.get(f"/events/{eid}/steps/audience").text

    def test_a_bad_custom_value_is_a_400_not_a_crash(self, client):
        eid = self._event(client)
        r = client.post(f"/events/{eid}/decide",
                        data={"step": "audience", "key": CUSTOM_KEY, "value": "abc"})
        assert r.status_code == 400


class TestCustomLabelReachesEverySurface:
    """display_label must be used wherever a chosen option is shown.

    Live check caught the playbook rendering a bare 'Custom' — the number the
    coordinator actually supplied was missing from the exported document, which
    is the one artifact they hand to other people.
    """

    def _planned(self, wf):
        eid = wf.start_event(name="Fleet Week", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key=CUSTOM_KEY, value="2450")
        wf.choose(eid, step="venue", key="austin-convention-center")
        return eid

    def test_playbook_section_label_shows_the_number(self, wf, conn):
        from app.features.playbook import compose_playbook

        eid = self._planned(wf)
        pb = compose_playbook(conn, eid)
        audience = next(s for s in pb.sections if s.step == "audience")
        assert audience.chosen_label == "Custom: 2,450 attendees"

    def test_playbook_markdown_shows_the_number(self, wf, conn):
        from app.features.playbook import compose_playbook, render_markdown

        eid = self._planned(wf)
        md = render_markdown(compose_playbook(conn, eid))
        assert "Custom: 2,450 attendees" in md

    def test_deck_decision_slide_shows_the_number(self, wf, conn):
        from app.features.deck import build_deck
        from app.features.images import ImageResolver
        from app.features.playbook import compose_playbook

        eid = self._planned(wf)
        deck = build_deck(compose_playbook(conn, eid), ImageResolver(None))
        slide = next(s for s in deck.slides if s.step == "audience")
        assert "2,450" in slide.title

    def test_preset_labels_are_unchanged_everywhere(self, wf, conn):
        from app.features.playbook import compose_playbook

        eid = wf.start_event(name="F", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        wf.choose(eid, step="audience", key="baseline")
        pb = compose_playbook(conn, eid)
        audience = next(s for s in pb.sections if s.step == "audience")
        assert audience.chosen_label == "Modelled estimate: 6,000 attendees"
