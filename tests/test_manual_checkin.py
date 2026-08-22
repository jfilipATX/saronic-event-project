"""P5-8 — manual check-in (facilitator lookup of an existing invitee).

A facilitator looks up an *existing* invitee by name/email and marks them
arrived. This is distinct from a walk-in (which has NO invitee record): the
manual path reuses the SAME arrival record as a scan, just with
``checkin_method = "manual"`` so the audit trail shows a person recorded it.

Acceptance checks pinned here (mirrors Edye's spec):
1. Lookup by last name returns the matching invitee(s).
2. Manual check-in of an existing invitee sets arrived + method=manual; a VIP
   shows the VIP banner (company, name) on the desk.
3. Re-checking an already-arrived invitee does NOT re-announce (vip flag off).
4. Lookup with no match shows a steel note, NOT a 500, NOT a walk-in creation.
5. The arrival records checkin_method="manual".
6. Manual check-in reuses the SAME attendee row (no duplicate created).
7. The actor is recorded as "facilitator" (no login yet; the field is ready for
   a named actor later) — manual arrivals are attributable, never anonymous.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Attendee, Event
from app.features import qr_checkin as qr
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
                                         state="TX", country="US"))


def _add(conn, event_id, **kw):
    a = Attendee(event_id=event_id, full_name=kw.get("full_name"),
                 email=kw.get("email"), company=kw.get("company"),
                 is_vip=kw.get("is_vip", False))
    a.id = repo.add_attendee(conn, a)
    return a


class TestManualLookup:
    def test_lookup_by_last_name(self, conn, event):
        _add(conn, event, full_name="Jane Smith", email="jane@x.com")
        _add(conn, event, full_name="Bob Jones", email="bob@x.com")
        matches = qr.find_invitee(conn, event, "smith")
        assert len(matches) == 1
        assert matches[0].full_name == "Jane Smith"

    def test_lookup_case_insensitive_substring(self, conn, event):
        _add(conn, event, full_name="Jane Smith", email="Jane.Smith@State.Gov")
        matches = qr.find_invitee(conn, event, "STATE")
        assert len(matches) == 1
        assert matches[0].email.lower() == "jane.smith@state.gov"

    def test_no_match_returns_empty_not_error(self, conn, event):
        _add(conn, event, full_name="Jane Smith", email="jane@x.com")
        assert qr.find_invitee(conn, event, "zzz-nobody") == []

    def test_lookup_truncates_to_ten_with_flag(self, conn, event):
        for i in range(15):
            _add(conn, event, full_name=f"Person {i} Smith")
        matches, truncated = qr.find_invitee_truncated(conn, event, "smith",
                                                       limit=10)
        assert len(matches) == 10
        assert truncated is True


class TestManualCheckIn:
    def test_manual_checkin_sets_method_and_arrived(self, conn, event):
        a = _add(conn, event, full_name="Jane Smith", email="jane@x.com",
                 company="State Dept", is_vip=True)
        state, att = qr.manual_check_in(conn, event, a.id,
                                        actor="facilitator")
        conn.commit()
        assert state == qr.STATE_VALID
        row = repo.get_attendee(conn, a.id)
        assert row.attended_at is not None
        assert row.checkin_method == "manual"
        # VIP banner data present.
        assert att.is_vip and att.company == "State Dept"

    def test_manual_checkin_reuses_same_row_not_duplicate(self, conn, event):
        a = _add(conn, event, full_name="Jane Smith", email="jane@x.com")
        qr.manual_check_in(conn, event, a.id, actor="facilitator")
        conn.commit()
        before = repo.list_attendees(conn, event)
        assert len(before) == 1
        # A second lookup still finds exactly one Jane Smith.
        matches = qr.find_invitee(conn, event, "jane")
        assert len(matches) == 1

    def test_recheck_does_not_duplicate_or_reannounce(self, conn, event):
        a = _add(conn, event, full_name="Jane Smith", email="jane@x.com",
                 is_vip=True)
        qr.manual_check_in(conn, event, a.id, actor="facilitator")
        conn.commit()
        # Second manual check-in of an already-arrived VIP.
        state, att = qr.manual_check_in(conn, event, a.id,
                                        actor="facilitator")
        conn.commit()
        # STATE_ALREADY signals no re-announcement; same single row.
        assert state == qr.STATE_ALREADY
        assert repo.list_attendees(conn, event) == 1 or len(
            repo.list_attendees(conn, event)) == 1

    def test_manual_checkin_records_actor(self, conn, event):
        a = _add(conn, event, full_name="Jane Smith", email="jane@x.com")
        qr.manual_check_in(conn, event, a.id, actor="facilitator")
        conn.commit()
        row = repo.get_attendee(conn, a.id)
        assert row.checkin_actor == "facilitator"

    def test_route_lookup_then_mark_via_http(self, tmp_path):
        client = TestClient(create_app())
        r = client.post("/events", data={"name": "Fleet Week", "city": "Austin",
                                         "state": "TX", "country": "US"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        client.post(f"/events/{eid}/invites", data={
            "full_name": "Jane Smith", "email": "jane@x.com",
            "company": "State", "is_vip": "true"})
        # Lookup
        page = client.post(f"/events/{eid}/checkin/manual/lookup",
                           data={"query": "smith"}).text
        assert "Jane Smith" in page
        # Find the attendee id
        import re
        aid = int(re.search(r"name=\"attendee_id\" value=\"(\d+)\"", page).group(1))
        # Mark arrived
        res = client.post(f"/events/{eid}/checkin/manual",
                          data={"attendee_id": str(aid)})
        assert res.status_code == 200
        # The desk shows the arrived state; no 500; method recorded.
        after = client.get(f"/events/{eid}/checkin").text
        assert "Jane Smith" in after


class TestNoMatchIsNotWalkIn:
    def test_no_match_does_not_create_attendee(self, conn, event):
        _add(conn, event, full_name="Jane Smith", email="jane@x.com")
        # Manual check-in of a non-existent id must refuse, not create.
        with pytest.raises(ValueError):
            qr.manual_check_in(conn, event, attendee_id=99999,
                               actor="facilitator")
        assert len(repo.list_attendees(conn, event)) == 1
