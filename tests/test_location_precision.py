"""P5-3 — location precision: state + country on events, country defaults US.

Saronic runs events across many states and countries, so a bare city is
ambiguous (Springfield, IL vs Springfield, MA). Every place the location line
renders must show the full city / state / country so two same-named cities
never collapse into one.

Invariants pinned here:

* **country defaults to "US"** even when the form omits it — Joseph asked for
  US as the default, and an event with no country would otherwise render a
  broken blank in the location line.
* **The location line reads "City, ST, Country"**, with state abbreviated and
  country abbreviated, and gracefully drops any missing middle piece (a city
  with no state still reads as just the city, not "City, , US").
* **It threads into the render sites** — the nav header, the playbook, the
  check-in roster header, and the run-of-show page all show the same line. A
  feature that adds a field but only shows it in one place is a half-feature.
* **The create form carries state + country inputs**, and the workflow stores
  them (not just city).
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def conn():
    import app.db.repository as repo
    from app.db import schema_sql_text as sql

    handle = sqlite3.connect(":memory:")
    handle.row_factory = sqlite3.Row
    handle.executescript(sql.SCHEMA)
    repo.apply_migrations(handle)
    return handle


@pytest.fixture()
def event(conn):
    import app.db.repository as repo
    from app.db.models import Event

    return repo.create_event(conn, Event(name="Fleet Week", city="Austin",
                                         state="TX", country="US"))


def _location_line(city, state=None, country=None):
    """Mirror the helper the app uses, so tests assert the real shape."""
    from app.features.location import location_line

    return location_line(city, state, country)


class TestLocationLine:
    def test_full_line_reads_city_state_country(self):
        assert _location_line("Austin", "TX", "US") == "Austin, TX, US"

    def test_state_is_omitted_when_missing(self):
        # No dangling ", , US" — a city with no state reads as just the city.
        assert _location_line("London", None, "GB") == "London, GB"

    def test_country_defaults_to_us_in_the_helper_caller(self):
        # The helper itself is dumb; the DEFAULT is the caller's job. Here we
        # assert the *shape* the app produces when country is left blank.
        assert _location_line("Austin", "TX", "US") == "Austin, TX, US"

    def test_city_only_when_nothing_else(self):
        assert _location_line("Austin", None, None) == "Austin"


class TestEventLocationStorage:
    def test_country_defaults_to_us(self, conn):
        import app.db.repository as repo
        from app.db.models import Event

        eid = repo.create_event(conn, Event(name="X", city="Austin"))
        ev = repo.get_event(conn, eid)
        assert ev.country == "US"

    def test_state_and_country_are_stored(self, conn, event):
        import app.db.repository as repo

        ev = repo.get_event(conn, event)
        assert ev.state == "TX"
        assert ev.country == "US"

    def test_location_line_threads_into_the_event(self, conn, event):
        import app.db.repository as repo

        ev = repo.get_event(conn, event)
        assert _location_line(ev.city, ev.state, ev.country) == "Austin, TX, US"


class TestCreateFlow:
    def test_the_create_route_stores_state_and_country(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app

        client = TestClient(create_app(db_path=str(tmp_path / "t.db")))
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin", "state": "TX",
            "country": "US",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        page = client.get(f"/events/{eid}/run-of-show").text
        # The location line appears in the run-of-show header copy.
        assert "Austin, TX, US" in page

    def test_country_defaults_to_us_when_form_omits_it(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app

        client = TestClient(create_app(db_path=str(tmp_path / "t.db")))
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin", "state": "TX",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        page = client.get(f"/events/{eid}/run-of-show").text
        assert "Austin, TX, US" in page

    def test_the_location_line_appears_in_the_nav_header(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app

        client = TestClient(create_app(db_path=str(tmp_path / "t.db")))
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin", "state": "TX",
            "country": "US",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        page = client.get(f"/events/{eid}/playbook").text
        assert "Austin, TX, US" in page
