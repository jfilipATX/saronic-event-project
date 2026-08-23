"""P6-5 — run-of-show timeline (single-event Gantt) + cross-event portfolio.

Both views are read-only over the same segment data as every other surface, so
they cannot disagree. These pin the colour-coding and positioning.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.db import repository as repo
from app.db.models import Event, Person, Segment
from app.features import run_of_show as ros
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


def _seed_segments(c, eid):
    pid = repo.add_person(c, Person(name="Sam", role="Ops"))
    repo.assign_staff(c, eid, pid, role="Lead")
    repo.add_segment(c, Segment(event_id=eid, title="Expo open",
        start="2026-03-14 09:00", end="2026-03-14 17:00", kind="floor",
        track="Floor", owner_ids=[pid]))
    repo.add_segment(c, Segment(event_id=eid, title="Keynote",
        start="2026-03-14 10:00", end="2026-03-14 11:00", kind="presentation",
        track="Program", owner_ids=[pid]))
    repo.add_segment(c, Segment(event_id=eid, title="VIP lunch",
        start="2026-03-14 12:00", end="2026-03-14 13:30", kind="dinner",
        track="VIP", owner_ids=[pid]))
    # a second day
    repo.add_segment(c, Segment(event_id=eid, title="Awards dinner",
        start="2026-03-15 19:00", end="2026-03-15 21:00", kind="dinner",
        track="VIP", owner_ids=[pid]))


def test_timeline_positions_and_colours():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    _seed_segments(c, eid)
    html = client.get(f"/events/{eid}/timeline").text
    # Each kind's colour appears in the rendered blocks.
    for kind in ("floor", "presentation", "dinner"):
        assert ros.KIND_COLORS[kind].lower() in html.lower()
    # The legend lists every kind label.
    assert "Presentation" in html and "Dinner" in html and "Expo floor" in html
    # Two distinct days render as day bands.
    assert "2026-03-14" in html and "2026-03-15" in html
    # First block (09:00) starts at/near left edge; last (21:00 on day 2) far right.
    assert "left:0.0%" in html or "left:0.00%" in html


def test_timeline_uses_real_positions_not_just_labels():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    _seed_segments(c, eid)
    # build_timeline computes left/width percentages by actual time.
    segs = repo.list_segments(c, eid)
    tl = ros.build_timeline(c, segs)
    day0 = tl["2026-03-14"]
    expo = next(b for b in day0 if b["title"] == "Expo open")
    keynote = next(b for b in day0 if b["title"] == "Keynote")
    # Expo (09–17) starts at 0%; keynote (10–11) starts later and is narrower.
    assert expo["left_pct"] == 0.0
    assert keynote["left_pct"] > 0
    assert keynote["width_pct"] < expo["width_pct"]


def test_portfolio_shows_kind_distribution_and_status():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    _seed_segments(c, eid)
    html = client.get("/portfolio").text
    assert "Fleet Week" in html
    # kind bars carry the colour for each present kind
    assert ros.KIND_COLORS["floor"].lower() in html.lower()
    assert ros.KIND_COLORS["presentation"].lower() in html.lower()
    # links to the timeline + playbook
    assert f"/events/{eid}/timeline" in html
    assert f"/events/{eid}/playbook" in html
