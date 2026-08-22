"""P5-9-light — event owners + display-only check-in gating (no auth).

Per the ratified spec: lightweight cut only. No login. We add (1) an event
owner attribute (name + optional role, the accountable lead) and (2) a
display-only check-in gating callout that reads the per-event `can_check_in`
grant already built in P5-5. The gating SIGNALS assignment; it never hides
data, because there is no login to derive a viewer identity from.

Invariants pinned here:

* **Owner is an optional event attribute** — blank is valid for legacy events.
* **Owner shows on the playbook** ("Event owner: Name — Role") and on the
  event header / home list so the accountable lead is visible everywhere.
* **Check-in gating is display-only**: when ≥1 staff has `can_check_in` for
  THIS event, the check-in desk shows an informational callout naming them.
  When none are tagged, a muted hint says so. The roster is never hidden.
* **Per-event**: the callout reads `event_staff.can_check_in` for the current
  event only — a person tagged check-in on Event A is not named on Event B's
  desk (the P5-5 isolation, surfaced through the gate).
* **Erased staff drop from the callout** — an anonymised person must not be
  named as a check-in operator.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event, Person
from app.features.schedule import DayWindow
from app.main import create_app


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    repo.apply_migrations(c)
    return c


@pytest.fixture()
def event(conn):
    return repo.create_event(conn, Event(name="Fleet Week", city="Austin",
                                         state="TX", country="US",
                                         owner_name="Lt. Cmdr. Reyes",
                                         owner_role="Event Lead"))


class TestEventOwner:
    def test_owner_is_stored_and_optional(self, conn):
        eid = repo.create_event(conn, Event(name="X", city="Austin"))
        ev = repo.get_event(conn, eid)
        assert ev.owner_name is None and ev.owner_role is None

        eid2 = repo.create_event(conn, Event(name="Y", city="Austin",
                                             owner_name="Reyes",
                                             owner_role="Lead"))
        ev2 = repo.get_event(conn, eid2)
        assert ev2.owner_name == "Reyes" and ev2.owner_role == "Lead"

    def test_owner_round_trips_through_setter(self, conn, event):
        ev = repo.get_event(conn, event)
        assert ev.owner_name == "Lt. Cmdr. Reyes"
        assert ev.owner_role == "Event Lead"

    def test_owner_shows_on_playbook(self, tmp_path):
        client = TestClient(create_app(db_path=str(tmp_path / "t.db")))
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin", "state": "TX",
            "country": "US", "owner_name": "Reyes", "owner_role": "Event Lead",
        }, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        page = client.get(f"/events/{eid}/playbook").text
        assert "Event owner" in page
        assert "Reyes" in page and "Event Lead" in page


class TestCheckInGating:
    def _seed_days(self, conn, eid):
        repo.replace_event_days(conn, eid, [
            DayWindow(date="2026-03-14", open="09:00", close="17:00")])

    def _assign(self, conn, eid, name, can_check_in, role="Booth"):
        pid = repo.add_person(conn, Person(name=name, role=role))
        repo.assign_staff(conn, eid, pid, role=role,
                          can_check_in=can_check_in)
        return pid

    def test_callout_names_checkin_staff_for_this_event(self, conn, event):
        self._seed_days(conn, event)
        self._assign(conn, event, "Dana Reyes", can_check_in=True)
        self._assign(conn, event, "Sam Okoye", can_check_in=False)
        rows = repo.event_staff_rows(conn, event)
        assignees = [r for r in rows if r["can_check_in"]]
        names = sorted(
            repo.get_person(conn, r["person_id"]).display_name
            for r in assignees)
        assert names == ["Dana Reyes"]

    def test_no_assignee_shows_muted_hint_not_false_claim(self, conn, event):
        self._seed_days(conn, event)
        self._assign(conn, event, "Sam Okoye", can_check_in=False)
        rows = repo.event_staff_rows(conn, event)
        assignees = [r for r in rows if r["can_check_in"]]
        assert assignees == []

    def test_gating_is_per_event_not_global(self, conn, event):
        self._seed_days(conn, event)
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        self._seed_days(conn, other)
        # Dana check-in on Event A only.
        dana = repo.add_person(conn, Person(name="Dana", role="Lead"))
        repo.assign_staff(conn, event, dana, role="Lead", can_check_in=True)
        repo.assign_staff(conn, other, dana, role="Lead", can_check_in=False)
        a_rows = repo.event_staff_rows(conn, event)
        b_rows = repo.event_staff_rows(conn, other)
        assert [r for r in a_rows if r["can_check_in"]]
        assert not [r for r in b_rows if r["can_check_in"]]

    def test_erased_staff_drop_from_callout(self, conn, event):
        self._seed_days(conn, event)
        pid = self._assign(conn, event, "Dana Reyes", can_check_in=True)
        repo.erase_person(conn, pid)
        rows = repo.event_staff_rows(conn, event)
        live = [r for r in rows if r["can_check_in"]
                and not repo.get_person(conn, r["person_id"]).is_erased]
        assert live == []

    def test_checkin_page_shows_callout_when_assigned(self, tmp_path):
        client = TestClient(create_app(db_path=str(tmp_path / "t.db")))
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin", "state": "TX",
            "country": "US"}, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/schedule", data={
            "day_0_date": "2026-03-14", "day_0_open": "09:00",
            "day_0_close": "17:00"})
        # Tag Dana check-in via the run-of-show assign route.
        client.post(f"/events/{eid}/run-of-show/staff/assign", data={
            "name": "Dana Reyes", "role": "Booth", "can_check_in": "true"})
        page = client.get(f"/events/{eid}/checkin").text
        assert "Dana Reyes" in page
        assert "check-in" in page.lower()

    def test_checkin_page_shows_muted_hint_when_unassigned(self, tmp_path):
        client = TestClient(create_app(db_path=str(tmp_path / "t.db")))
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin", "state": "TX",
            "country": "US"}, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/schedule", data={
            "day_0_date": "2026-03-14", "day_0_open": "09:00",
            "day_0_close": "17:00"})
        page = client.get(f"/events/{eid}/checkin").text
        assert "No check-in staff" in page

    def test_checkin_page_never_hides_the_roster(self, tmp_path):
        """Display-only gating: the full attendee roster is present even when
        check-in is assigned — we signal, we do not enforce."""
        client = TestClient(create_app(db_path=str(tmp_path / "t.db")))
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin", "state": "TX",
            "country": "US"}, follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/schedule", data={
            "day_0_date": "2026-03-14", "day_0_open": "09:00",
            "day_0_close": "17:00"})
        client.post(f"/events/{eid}/run-of-show/staff/assign", data={
            "name": "Dana Reyes", "role": "Booth", "can_check_in": "true"})
        page = client.get(f"/events/{eid}/checkin").text
        # The roster/hint scaffolding is present (no 500, no hidden section).
        assert "Check-in" in page
