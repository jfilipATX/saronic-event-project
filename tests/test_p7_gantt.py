"""P7-3 — Gantt shows owner Name · Role and a per-day jump nav.

Owner labels become "Name · Role" (the user's chosen format); multi-day events
render one band per day with anchor ids + day-jump links so there's no giant
blank gap between non-adjacent days.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.db import repository as repo
from app.db.models import Event, Person, Segment
from app.main import create_app


def _client_db():
    d = tempfile.mkdtemp()
    os.environ["DB_PATH"] = os.path.join(d, "t.db")
    return TestClient(create_app())


def _make(client):
    r = client.post("/events", data={"name": "FW", "city": "SF", "state": "CA",
                                     "country": "US"}, follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


def _direct():
    c = sqlite3.connect(os.environ["DB_PATH"], isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def _seed(c, eid):
    pid = repo.add_person(c, Person(name="Sam", role="Ops"))
    repo.assign_staff(c, eid, pid, role="Ops")
    repo.add_segment(c, Segment(event_id=eid, title="Expo",
        start="2026-05-08 09:00", end="2026-05-08 18:00", kind="floor",
        track="Floor", owner_ids=[pid]))
    repo.add_segment(c, Segment(event_id=eid, title="Dinner",
        start="2026-05-09 19:00", end="2026-05-09 21:00", kind="dinner",
        track="VIP", owner_ids=[pid]))


def test_gantt_owner_shows_name_and_role():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    _seed(c, eid)
    html = client.get(f"/events/{eid}/timeline").text
    assert "Sam · Ops" in html


def test_gantt_has_per_day_jump_nav_and_anchors():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    _seed(c, eid)
    html = client.get(f"/events/{eid}/timeline").text
    # day-jump links + anchors for both days
    assert 'href="#day-2026-05-08"' in html
    assert 'id="day-2026-05-09"' in html
    assert "day-jump" in html
