"""P6-1 — event lifecycle: complete, archive (recoverable), delete (anonymized
stub that destroys PII but keeps the row for counts).

Mirrors the three-way user split:
- Complete  = status flag, fully usable.
- Archive   = soft-hide, recoverable, PII untouched.
- Delete    = anonymized stub: PII wiped, row hidden, decisions/segments kept.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.db import repository as repo
from app.db.models import Attendee, Event, Person, Segment
from app.main import create_app


def _client_db():
    d = tempfile.mkdtemp()
    os.environ["DB_PATH"] = os.path.join(d, "t.db")
    return TestClient(create_app())


def _make(client):
    r = client.post("/events", data={"name": "Fleet Week", "city": "Austin",
                                     "state": "TX", "country": "US"},
                    follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


def _direct():
    c = sqlite3.connect(os.environ["DB_PATH"], isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def test_complete_sets_flag():
    client = _client_db()
    eid = _make(client)
    assert client.post(f"/events/{eid}/complete", follow_redirects=False).status_code == 303
    c = _direct()
    assert repo.get_event(c, eid).status == "complete"
    # still usable: playbook renders
    assert client.get(f"/events/{eid}/playbook").status_code == 200


def test_archive_hides_then_unarchive_restores():
    client = _client_db()
    eid = _make(client)
    assert client.post(f"/events/{eid}/archive", follow_redirects=False).status_code == 303
    c = _direct()
    assert repo.get_event(c, eid).status == "archived"
    # hidden from the visible list (only archived/deleted are excluded)
    assert eid not in [e.id for e in repo.list_events_visible(c)]
    # recoverable
    assert client.post(f"/events/{eid}/unarchive", follow_redirects=False).status_code == 303
    assert repo.get_event(c, eid).status == "active"
    assert eid in [e.id for e in repo.list_events_visible(c)]


def test_delete_wipes_pii_but_keeps_counts():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    pid = repo.add_person(c, Person(name="Sam", role="Ops"))
    repo.assign_staff(c, eid, pid, role="Lead", can_check_in=False)
    repo.add_segment(c, Segment(event_id=eid, title="Briefing",
                                start="2026-03-14 10:00", end="2026-03-14 11:00",
                                track="Ops", owner_ids=[pid]))
    repo.add_attendee(c, Attendee(event_id=eid, full_name="Jane Smith",
                                  email="jane@x.com", company="State Dept",
                                  is_vip=True))
    assert client.post(f"/events/{eid}/delete", follow_redirects=False).status_code == 303
    # PII destroyed
    att = c.execute("SELECT full_name, email FROM attendees WHERE event_id=?",
                    (eid,)).fetchone()
    assert att["full_name"] == "[removed]" and att["email"] is None
    # Event row retained (not dropped) + hidden
    ev = repo.get_event(c, eid)
    assert ev is not None and ev.status == "deleted"
    assert eid not in [e.id for e in repo.list_events_visible(c)]
    # counts preserved: decision/segment rows survive
    assert c.execute("SELECT count(*) FROM segments WHERE event_id=?",
                     (eid,)).fetchone()[0] == 1


def test_delete_is_distinct_from_archive_pii_untouched():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    repo.add_attendee(c, Attendee(event_id=eid, full_name="Jane Smith",
                                  email="jane@x.com"))
    client.post(f"/events/{eid}/archive", follow_redirects=False)
    att = c.execute("SELECT full_name FROM attendees WHERE event_id=?",
                    (eid,)).fetchone()
    assert att["full_name"] == "Jane Smith"  # archive keeps PII
