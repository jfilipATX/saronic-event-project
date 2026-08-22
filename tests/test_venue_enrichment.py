"""P2-3 — venue enrichment: links, map, favourites, and used-before history.

Design rules this pins:

* **Favourites must never re-sort above fit.** A favourite is a visible marker,
  not a ranking input — otherwise we have built a soft pre-decide that biases the
  coordinator, which is the thing this whole tool exists not to do.
* **History keys on a stable venue id, not the display name.** A renamed venue
  must keep its history; two venues sharing a name in different cities must not
  merge.
* Links are reference, not actions: they ride in the option data so the card can
  render them quietly.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event, VenueUse
from app.features.venue_options import build_venue_options, venue_id
from app.providers.base import Venue

AUSTIN = [
    Venue("Austin Convention Center", "Austin", 9000, 4.6, "Downtown.",
          website="https://www.austinconventioncenter.com",
          latitude=30.2639, longitude=-97.7397),
    Venue("Palmer Events Center", "Austin", 3000, 4.4, "Lake views.",
          website="https://www.palmereventscenter.com",
          latitude=30.2578, longitude=-97.7545),
    Venue("Fairmont Austin", "Austin", 1800, 4.5, "Hotel ballroom."),
]


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


class TestVenueIdentity:
    def test_id_is_stable_for_the_same_venue(self):
        assert venue_id(AUSTIN[0]) == venue_id(AUSTIN[0])

    def test_id_distinguishes_same_name_in_different_cities(self):
        a = Venue("Grand Hall", "Austin", 1000, 4.0, "")
        b = Venue("Grand Hall", "San Diego", 1000, 4.0, "")
        assert venue_id(a) != venue_id(b)

    def test_id_survives_a_rename(self):
        """History must not evaporate when a venue is rebranded."""
        original = Venue("Palmer Events Center", "Austin", 3000, 4.4, "")
        # The provider pins the ref to the ORIGINAL default, so the rebranded
        # venue resolves to the same identity and keeps its history.
        renamed = Venue("Palmer Events Centre", "Austin", 3000, 4.4, "",
                        venue_ref="palmer-events-center--austin")
        assert venue_id(renamed) == venue_id(original)

    def test_id_is_url_safe(self):
        assert venue_id(AUSTIN[0]) == "austin-convention-center--austin"


class TestLinksOnOptions:
    def test_website_is_carried_when_known(self):
        opts = build_venue_options(AUSTIN, audience=2000)
        acc = next(o for o in opts if o.label == "Austin Convention Center")
        assert acc.data["website"] == "https://www.austinconventioncenter.com"

    def test_map_url_is_derived_from_coordinates(self):
        opts = build_venue_options(AUSTIN, audience=2000)
        acc = next(o for o in opts if o.label == "Austin Convention Center")
        assert acc.data["map_url"].startswith("https://www.openstreetmap.org/")
        assert "30.2639" in acc.data["map_url"]

    def test_venue_without_coordinates_falls_back_to_a_name_search(self):
        opts = build_venue_options(AUSTIN, audience=2000)
        fairmont = next(o for o in opts if o.label == "Fairmont Austin")
        assert "openstreetmap.org/search" in fairmont.data["map_url"]
        assert "Fairmont" in fairmont.data["map_url"]

    def test_missing_website_is_absent_not_empty_string(self):
        opts = build_venue_options(AUSTIN, audience=2000)
        fairmont = next(o for o in opts if o.label == "Fairmont Austin")
        assert fairmont.data.get("website") is None


class TestFavouritesDoNotOutrankFit:
    def test_favourite_is_marked_on_the_option(self, conn):
        repo.set_favourite(conn, "palmer-events-center--austin", True)
        opts = build_venue_options(AUSTIN, audience=6000,
                                   favourites=repo.favourites(conn))
        palmer = next(o for o in opts if o.label == "Palmer Events Center")
        assert palmer.data["favourite"] is True

    def test_a_favourite_under_capacity_still_sorts_below_a_fitting_venue(self, conn):
        """The critical rule: fit is the primary sort, always."""
        repo.set_favourite(conn, "palmer-events-center--austin", True)
        opts = build_venue_options(AUSTIN, audience=6000,
                                   favourites=repo.favourites(conn))
        assert opts[0].label == "Austin Convention Center"   # fits, not favourite
        assert opts[0].data["favourite"] is False

    def test_favourite_breaks_ties_within_the_same_fit_band(self, conn):
        """Within equal fit a favourite may surface first — that is a marker,
        not a pre-decide, because it cannot cross a fit boundary."""
        repo.set_favourite(conn, "fairmont-austin", True)
        opts = build_venue_options(AUSTIN, audience=6000,
                                   favourites=repo.favourites(conn))
        under = [o for o in opts if o.data["fit"] == "under"]
        assert under[0].label == "Fairmont Austin"

    def test_no_favourites_leaves_ordering_untouched(self, conn):
        plain = build_venue_options(AUSTIN, audience=6000)
        withf = build_venue_options(AUSTIN, audience=6000, favourites=set())
        assert [o.label for o in plain] == [o.label for o in withf]

    def test_toggling_off_removes_the_marker(self, conn):
        repo.set_favourite(conn, "palmer-events-center--austin", True)
        repo.set_favourite(conn, "palmer-events-center--austin", False)
        assert repo.favourites(conn) == set()


class TestUsedBeforeHistory:
    def test_recording_a_use_makes_it_retrievable(self, conn):
        eid = repo.create_event(conn, Event(name="Corsair Demo Day", city="Austin"))
        repo.record_venue_use(conn, VenueUse(
            venue_ref="palmer-events-center--austin", event_id=eid,
            event_name="Corsair Demo Day", used_on="2025-09-14"))
        uses = repo.venue_uses(conn)
        assert uses["palmer-events-center--austin"][0].event_name == "Corsair Demo Day"

    def test_history_appears_in_the_option_reasoning(self, conn):
        eid = repo.create_event(conn, Event(name="Corsair Demo Day", city="Austin"))
        repo.record_venue_use(conn, VenueUse(
            venue_ref="palmer-events-center--austin", event_id=eid,
            event_name="Corsair Demo Day", used_on="2025-09-14"))
        opts = build_venue_options(AUSTIN, audience=2000,
                                   history=repo.venue_uses(conn))
        palmer = next(o for o in opts if o.label == "Palmer Events Center")
        assert "Corsair Demo Day" in palmer.reasoning
        assert "2025" in palmer.reasoning

    def test_history_is_exposed_as_structured_data_too(self, conn):
        eid = repo.create_event(conn, Event(name="Demo", city="Austin"))
        repo.record_venue_use(conn, VenueUse(
            venue_ref="palmer-events-center--austin", event_id=eid,
            event_name="Demo", used_on="2025-09-14"))
        opts = build_venue_options(AUSTIN, audience=2000,
                                   history=repo.venue_uses(conn))
        palmer = next(o for o in opts if o.label == "Palmer Events Center")
        assert palmer.data["used_before"] == 1

    def test_multiple_uses_are_summarised_most_recent_first(self, conn):
        eid = repo.create_event(conn, Event(name="E", city="Austin"))
        for name, when in (("Older Event", "2024-03-01"), ("Newer Event", "2025-09-14")):
            repo.record_venue_use(conn, VenueUse(
                venue_ref="palmer-events-center--austin", event_id=eid,
                event_name=name, used_on=when))
        opts = build_venue_options(AUSTIN, audience=2000,
                                   history=repo.venue_uses(conn))
        palmer = next(o for o in opts if o.label == "Palmer Events Center")
        assert palmer.data["used_before"] == 2
        # The sentence names the most recent event and counts the rest, rather
        # than listing every past booking on a decision card.
        assert "Newer Event" in palmer.reasoning
        assert "1 other event" in palmer.reasoning
        assert "Older Event" not in palmer.reasoning

    def test_history_does_not_change_the_sort(self, conn):
        """Used-before is context, not a ranking signal."""
        eid = repo.create_event(conn, Event(name="E", city="Austin"))
        repo.record_venue_use(conn, VenueUse(
            venue_ref="fairmont-austin", event_id=eid,
            event_name="Old Event", used_on="2024-01-01"))
        plain = [o.label for o in build_venue_options(AUSTIN, audience=6000)]
        withh = [o.label for o in build_venue_options(
            AUSTIN, audience=6000, history=repo.venue_uses(conn))]
        assert plain == withh

    def test_unused_venue_says_nothing_about_history(self, conn):
        opts = build_venue_options(AUSTIN, audience=2000, history={})
        acc = next(o for o in opts if o.label == "Austin Convention Center")
        assert acc.data["used_before"] == 0
        assert "previously" not in acc.reasoning.lower()


class TestLegacyDatabaseGetsTheNewTables:
    def test_migration_adds_venue_tables(self, tmp_path):
        path = str(tmp_path / "old.db")
        c = sqlite3.connect(path)
        c.executescript(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, city TEXT, created_at TEXT, "
            "audience_estimate INTEGER, event_type TEXT);")
        c.commit()
        c.close()
        repo.init_db(path)
        c = sqlite3.connect(path)
        try:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()
        assert {"venue_favourites", "venue_uses"} <= tables


class TestVenueEnrichmentInTheUi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    def _at_venue_step(self, client) -> int:
        r = client.post("/events", data={"name": "E", "city": "Austin"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/decide",
                    data={"step": "event_type", "key": "convention"})
        client.post(f"/events/{eid}/decide",
                    data={"step": "audience", "key": "baseline"})
        return eid

    def test_venue_cards_show_website_and_map_links(self, client):
        eid = self._at_venue_step(client)
        page = client.get(f"/events/{eid}/steps/venue").text
        assert "austinconventioncenter.com" in page
        assert "openstreetmap.org" in page
        assert "link-accent" in page

    def test_favourite_toggles_and_persists(self, client):
        eid = self._at_venue_step(client)
        r = client.post(f"/events/{eid}/venues/palmer-events-center--austin/favourite",
                        data={"on": "1"}, follow_redirects=False)
        assert r.status_code in (302, 303)
        assert "★ Favourite" in client.get(f"/events/{eid}/steps/venue").text

    def test_favouriting_does_not_disturb_an_existing_choice(self, client):
        eid = self._at_venue_step(client)
        client.post(f"/events/{eid}/decide",
                    data={"step": "venue", "key": "austin-convention-center"})
        client.post(f"/events/{eid}/venues/palmer-events-center--austin/favourite",
                    data={"on": "1"})
        page = client.get(f"/events/{eid}/steps/venue").text
        assert "is-chosen" in page

    def test_favourite_does_not_reorder_above_a_better_fit(self, client):
        eid = self._at_venue_step(client)
        client.post(f"/events/{eid}/venues/fairmont-austin/favourite",
                    data={"on": "1"})
        page = client.get(f"/events/{eid}/steps/venue").text
        assert page.index("Austin Convention Center") < page.index("Fairmont Austin")
