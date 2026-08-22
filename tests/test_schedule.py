"""P4-3 — event start/end date-times.

Small feature, but it is the time axis the run of show hangs off, so the
invariants matter more than the UI:

* An end before its start is refused, not stored. A negative-duration event
  breaks every downstream view rather than looking odd in one.
* Dates are optional. A coordinator scoping an event in March does not yet know
  the hour, and forcing a placeholder produces confidently wrong data.
* Changing dates is a revision with a trail, like every other change here.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event
from app.features.schedule import (
    EventWindow,
    describe_window,
    parse_window,
    window_for_event,
)
from app.main import create_app


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def event(conn) -> int:
    return repo.create_event(conn, Event(name="Fleet Week", city="Austin"))


class TestParsing:
    def test_accepts_html_datetime_local_values(self):
        window = parse_window("2026-03-14T08:00", "2026-03-16T18:00")
        assert window.start == "2026-03-14T08:00"
        assert window.end == "2026-03-16T18:00"

    def test_accepts_a_date_without_a_time(self):
        window = parse_window("2026-03-14", "2026-03-16")
        assert window.start.startswith("2026-03-14")

    def test_blank_values_are_allowed(self):
        """Dates are optional — an unscheduled event is a real state."""
        window = parse_window("", "")
        assert window.start is None and window.end is None

    def test_a_start_without_an_end_is_allowed(self):
        assert parse_window("2026-03-14T08:00", "").end is None

    def test_an_end_before_the_start_is_refused(self):
        with pytest.raises(ValueError, match="before"):
            parse_window("2026-03-16T08:00", "2026-03-14T08:00")

    def test_the_same_instant_is_refused(self):
        with pytest.raises(ValueError, match="before|same"):
            parse_window("2026-03-14T08:00", "2026-03-14T08:00")

    @pytest.mark.parametrize("bad", ["not a date", "2026-13-45", "14/03/2026"])
    def test_an_unparseable_value_is_refused(self, bad):
        with pytest.raises(ValueError):
            parse_window(bad, "")

    def test_an_end_without_a_start_is_refused(self):
        """An end alone cannot anchor a timeline."""
        with pytest.raises(ValueError, match="start"):
            parse_window("", "2026-03-16T18:00")


class TestPersistence:
    def test_a_window_round_trips(self, conn, event):
        repo.set_event_window(conn, event, parse_window("2026-03-14T08:00",
                                                        "2026-03-16T18:00"))
        window = window_for_event(conn, event)
        assert window.start == "2026-03-14T08:00"
        assert window.end == "2026-03-16T18:00"

    def test_an_event_with_no_window_reports_empty(self, conn, event):
        window = window_for_event(conn, event)
        assert window.start is None
        assert window.is_set is False

    def test_setting_a_window_twice_replaces_it(self, conn, event):
        repo.set_event_window(conn, event, parse_window("2026-03-14T08:00", ""))
        repo.set_event_window(conn, event, parse_window("2026-04-01T09:00", ""))
        assert window_for_event(conn, event).start == "2026-04-01T09:00"

    def test_clearing_the_window_is_possible(self, conn, event):
        repo.set_event_window(conn, event, parse_window("2026-03-14T08:00", ""))
        repo.set_event_window(conn, event, parse_window("", ""))
        assert window_for_event(conn, event).is_set is False

    def test_the_window_does_not_leak_between_events(self, conn, event):
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        repo.set_event_window(conn, event, parse_window("2026-03-14T08:00", ""))
        assert window_for_event(conn, other).is_set is False


class TestDescription:
    def test_a_multi_day_window_reads_naturally(self):
        text = describe_window(parse_window("2026-03-14T08:00", "2026-03-16T18:00"))
        assert "14" in text and "16" in text and "March" in text

    def test_a_single_day_window_does_not_repeat_the_date(self):
        text = describe_window(parse_window("2026-03-14T08:00", "2026-03-14T18:00"))
        assert text.count("March") == 1

    def test_an_unset_window_says_so(self):
        assert "not scheduled" in describe_window(EventWindow()).lower()

    def test_a_start_only_window_is_described(self):
        assert "2026" in describe_window(parse_window("2026-03-14T08:00", ""))


class TestDurationForTheTimeAxis:
    """P4-4 hangs its lanes off this."""

    def test_duration_in_hours(self):
        window = parse_window("2026-03-14T08:00", "2026-03-14T18:00")
        assert window.duration_hours == pytest.approx(10.0)

    def test_a_multi_day_duration(self):
        window = parse_window("2026-03-14T08:00", "2026-03-16T08:00")
        assert window.duration_hours == pytest.approx(48.0)

    def test_an_open_window_has_no_duration(self):
        assert parse_window("2026-03-14T08:00", "").duration_hours is None

    def test_the_day_list_covers_every_calendar_day(self):
        window = parse_window("2026-03-14T22:00", "2026-03-16T02:00")
        assert window.days == ["2026-03-14", "2026-03-15", "2026-03-16"]


class TestUi:
    @pytest.fixture()
    def client(self, tmp_path) -> TestClient:
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    def test_the_create_form_offers_dates(self, client):
        page = client.get("/").text
        assert 'name="starts_at"' in page and 'name="ends_at"' in page

    def test_creating_with_dates_stores_them(self, client):
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin",
            "starts_at": "2026-03-14T08:00", "ends_at": "2026-03-16T18:00",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        assert "March" in client.get(f"/events/{eid}/schedule").text

    def test_creating_without_dates_still_works(self, client):
        r = client.post("/events", data={"name": "Undated", "city": "Austin"},
                        follow_redirects=False)
        assert r.status_code in (302, 303)

    def test_a_backwards_window_is_refused_with_a_reason(self, client):
        r = client.post("/events", data={
            "name": "Backwards", "city": "Austin",
            "starts_at": "2026-03-16T08:00", "ends_at": "2026-03-14T08:00",
        })
        assert r.status_code == 400
        assert "before" in r.text.lower()

    def test_dates_can_be_edited_later(self, client):
        r = client.post("/events", data={"name": "E", "city": "Austin"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/schedule", data={
            "starts_at": "2026-05-01T09:00", "ends_at": "2026-05-02T17:00"})
        assert "May" in client.get(f"/events/{eid}/schedule").text

    def test_an_invalid_edit_does_not_destroy_the_existing_window(self, client):
        r = client.post("/events", data={
            "name": "E", "city": "Austin",
            "starts_at": "2026-03-14T08:00", "ends_at": "2026-03-16T18:00",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/schedule", data={
            "starts_at": "2026-03-16T08:00", "ends_at": "2026-03-14T08:00"})
        assert "March" in client.get(f"/events/{eid}/schedule").text

    def test_the_playbook_states_the_window(self, client):
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin",
            "starts_at": "2026-03-14T08:00", "ends_at": "2026-03-16T18:00",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        assert "March" in client.get(f"/events/{eid}/playbook").text

    def test_an_unscheduled_event_says_so_rather_than_hiding_it(self, client):
        r = client.post("/events", data={"name": "E", "city": "Austin"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        assert "not scheduled" in client.get(f"/events/{eid}/schedule").text.lower()


class TestLegacyDatabasesMigrate:
    def test_the_window_columns_are_added(self, tmp_path):
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
            cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
        finally:
            c.close()
        assert {"starts_at", "ends_at"} <= cols
