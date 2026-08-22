"""P2-1 — the create screen: paste a URL, confirm the facts, start the event.

The whole point is that a scrape never becomes an event on its own. The URL
produces *proposals*; the coordinator confirms or corrects each one; only then
does anything get written. And a failed scrape must never be a dead end —
manual entry is always right there.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.db import repository as repo
from app.main import create_app

PAGE = (
    "<html><head><title>Fleet Week</title></head><body>"
    "<h1>Saronic Fleet Week 2026</h1>"
    "<p>14&ndash;16 March 2026 &middot; Rotterdam Ahoy, Rotterdam, Netherlands</p>"
    "<p>Expected attendance: 4,200 professionals.</p></body></html>"
)

FACTS_JSON = (
    '{"event_name":"Saronic Fleet Week 2026","start_date":"2026-03-14",'
    '"end_date":"2026-03-16","city":"Rotterdam","country":"Netherlands",'
    '"venue":"Rotterdam Ahoy","expected_attendance":4200}'
)


class _Claude:
    def __init__(self, response=FACTS_JSON):
        self.response = response

    def complete(self, **kw):
        return self.response


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(db_path=str(tmp_path / "t.db")))


@pytest.fixture()
def scraping(monkeypatch):
    """Wire a successful fetch + extraction without touching the network."""
    from app.features.url_fetch import FetchResult

    monkeypatch.setattr(
        main_mod, "fetch_url",
        lambda url, **kw: FetchResult(ok=True, text=PAGE,
                                      final_url="https://ahoy.test/fleet-week"))
    monkeypatch.setattr(main_mod, "_scrape_client", lambda: _Claude())


class TestCreateScreenOffersUrlIntake:
    def test_home_offers_a_url_field(self, client):
        assert 'name="event_url"' in client.get("/").text

    def test_manual_entry_still_works_untouched(self, client):
        r = client.post("/events", data={"name": "Manual", "city": "Austin"},
                        follow_redirects=False)
        assert r.status_code in (302, 303)
        assert "/steps/event_type" in r.headers["location"]


class TestScrapeProposesFacts:
    def test_pasting_a_url_shows_a_confirm_screen(self, client, scraping):
        r = client.post("/events/scrape", data={"event_url": "https://ahoy.test/e"},
                        follow_redirects=False)
        assert r.status_code == 200
        assert "Rotterdam" in r.text

    def test_nothing_is_created_before_confirmation(self, client, scraping, tmp_path):
        client.post("/events/scrape", data={"event_url": "https://ahoy.test/e"})
        assert "Saronic Fleet Week 2026" not in client.get("/").text

    def test_every_proposed_fact_is_editable(self, client, scraping):
        r = client.post("/events/scrape", data={"event_url": "https://ahoy.test/e"})
        assert r.text.count('name="fact_') >= 5

    def test_the_source_is_attributed_on_screen(self, client, scraping):
        r = client.post("/events/scrape", data={"event_url": "https://ahoy.test/e"})
        assert "ahoy.test" in r.text

    def test_confirming_creates_the_event_with_the_confirmed_values(self, client, scraping):
        r = client.post("/events", data={
            "name": "Saronic Fleet Week 2026", "city": "Rotterdam",
            "source_url": "https://ahoy.test/fleet-week",
            "fact_country": "Netherlands", "fact_venue": "Rotterdam Ahoy",
            "fact_start_date": "2026-03-14",
        }, follow_redirects=False)
        assert r.status_code in (302, 303)
        assert "Saronic Fleet Week 2026" in client.get("/").text

    def test_confirmed_facts_are_stored_as_event_variables(self, client, scraping, tmp_path):
        import sqlite3

        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Rotterdam",
            "source_url": "https://ahoy.test/fleet-week",
            "fact_country": "Netherlands", "fact_venue": "Rotterdam Ahoy",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        conn = sqlite3.connect(main_mod.CURRENT_DB)
        conn.row_factory = sqlite3.Row
        try:
            kinds = {v.kind: v for v in repo.list_variables(conn, eid)}
        finally:
            conn.close()
        assert kinds["country"].value == "Netherlands"
        assert kinds["venue"].value == "Rotterdam Ahoy"

    def test_a_corrected_value_is_what_gets_stored(self, client, scraping):
        """The coordinator's edit wins over the scrape, always."""
        import sqlite3

        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Rotterdam",
            "source_url": "https://ahoy.test/fleet-week",
            "fact_venue": "Ahoy Rotterdam (corrected)",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        conn = sqlite3.connect(main_mod.CURRENT_DB)
        conn.row_factory = sqlite3.Row
        try:
            venue = {v.kind: v.value for v in repo.list_variables(conn, eid)}["venue"]
        finally:
            conn.close()
        assert venue == "Ahoy Rotterdam (corrected)"

    def test_blank_facts_are_not_stored(self, client, scraping):
        import sqlite3

        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Rotterdam",
            "fact_country": "", "fact_venue": "   ",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        conn = sqlite3.connect(main_mod.CURRENT_DB)
        conn.row_factory = sqlite3.Row
        try:
            assert repo.list_variables(conn, eid) == []
        finally:
            conn.close()

    def test_the_source_url_is_recorded_for_traceability(self, client, scraping):
        import sqlite3

        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Rotterdam",
            "source_url": "https://ahoy.test/fleet-week",
            "fact_venue": "Rotterdam Ahoy",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        conn = sqlite3.connect(main_mod.CURRENT_DB)
        conn.row_factory = sqlite3.Row
        try:
            kinds = {v.kind for v in repo.list_variables(conn, eid)}
        finally:
            conn.close()
        assert "source_url" in kinds


class TestFailureIsNeverADeadEnd:
    def test_an_unsafe_url_explains_and_offers_manual_entry(self, client):
        r = client.post("/events/scrape",
                        data={"event_url": "http://169.254.169.254/latest"})
        assert r.status_code == 200
        assert "manually" in r.text.lower()
        assert 'name="name"' in r.text        # the manual form is right there

    def test_a_failed_fetch_explains_and_offers_manual_entry(self, client, monkeypatch):
        from app.features.url_fetch import FetchResult

        monkeypatch.setattr(
            main_mod, "fetch_url",
            lambda url, **kw: FetchResult(ok=False,
                                          error="Could not reach that page. "
                                                "Enter the event details manually below."))
        r = client.post("/events/scrape", data={"event_url": "https://dead.test/e"})
        assert r.status_code == 200
        assert "manually" in r.text.lower()

    def test_extraction_finding_nothing_still_offers_manual_entry(self, client, monkeypatch):
        from app.features.url_fetch import FetchResult

        monkeypatch.setattr(
            main_mod, "fetch_url",
            lambda url, **kw: FetchResult(ok=True, text="<p>nothing here</p>",
                                          final_url="https://x.test/e"))
        monkeypatch.setattr(main_mod, "_scrape_client",
                            lambda: _Claude("no json at all"))
        r = client.post("/events/scrape", data={"event_url": "https://x.test/e"})
        assert r.status_code == 200
        assert "manually" in r.text.lower() or "nothing" in r.text.lower()

    def test_no_claude_configured_still_offers_manual_entry(self, client, monkeypatch):
        from app.features.url_fetch import FetchResult

        monkeypatch.setattr(
            main_mod, "fetch_url",
            lambda url, **kw: FetchResult(ok=True, text=PAGE, final_url="https://x.test/e"))
        monkeypatch.setattr(main_mod, "_scrape_client", lambda: None)
        r = client.post("/events/scrape", data={"event_url": "https://x.test/e"})
        assert r.status_code == 200
        assert 'name="name"' in r.text

    def test_an_empty_url_is_not_a_crash(self, client):
        r = client.post("/events/scrape", data={"event_url": ""})
        assert r.status_code == 200
