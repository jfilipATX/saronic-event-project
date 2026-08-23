"""P7-4 — per-day Gantt charts appended to the playbook print view.

The coordinator can skip printing this section; when printed, the block colors
survive (print-color-adjust: exact) so the wall chart stays readable.
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


def test_print_view_includes_per_day_gantt():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    _seed(c, eid)
    html = client.get(f"/events/{eid}/playbook/print").text
    assert "Run-of-show timeline (per day)" in html
    assert 'class="timeline-block"' in html
    # color-coded blocks carry an inline background
    assert "background:" in html
    # skippable note present
    assert "skip printing" in html
