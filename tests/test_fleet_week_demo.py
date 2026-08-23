"""P6-6 — Fleet Week demo seed.

Synthesizes a fully-fleshed, labeled Demo event (hybrid: scraped Fleet Week
structure + synthesized specifics). It must be flagged is_demo, carry a multi-day
run of show, decisions, staff, attendees, and example imagery — and be reachable
from the UI via the "Load Fleet Week demo" button.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.db import repository as repo
from app.features.demo_seed import seed_fleet_week_demo
from app.main import create_app


def _client_db():
    d = tempfile.mkdtemp()
    os.environ["DB_PATH"] = os.path.join(d, "t.db")
    return TestClient(create_app())


def _direct():
    c = sqlite3.connect(os.environ["DB_PATH"], isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def test_seed_creates_labeled_demo_with_full_content():
    client = _client_db()
    c = _direct()
    repo.init_db(os.environ["DB_PATH"])
    eid = seed_fleet_week_demo(c)
    ev = repo.get_event(c, eid)
    # Labeled as demo.
    assert ev.is_demo
    assert ev.name == "Fleet Week — Demo"
    # Multi-day window + day windows.
    assert ev.starts_at and ev.ends_at
    days = repo.event_days(c, eid)
    assert len(days) == 3
    # Decisions settled (playbook has content).
    assert len(repo.current_decisions(c, eid)) >= 3
    # Staff + attendees.
    assert len(repo.event_staff_rows(c, eid)) >= 4
    assert len(repo.list_attendees(c, eid)) >= 3
    # Run of show spans 3 days with mixed kinds.
    segs = repo.list_segments(c, eid)
    assert len(segs) >= 10
    kinds = {s.kind for s in segs}
    assert {"floor", "presentation", "dinner", "panel", "booth", "visitor", "program"} <= kinds
    # Example imagery seeded.
    assert any(im.origin == "example" for im in repo.library_images(c, eid))


def test_demo_button_creates_demo_event_reachable_from_home():
    client = _client_db()
    resp = client.post("/demo/load-fleet-week", follow_redirects=False)
    assert resp.status_code == 303
    home = client.get("/").text
    assert "Fleet Week — Demo" in home
    assert "Demo" in home  # the badge
    # The demo shows on the portfolio + timeline without error.
    eid = _direct().execute(
        "SELECT id FROM events WHERE is_demo=1").fetchone()["id"]
    assert client.get(f"/events/{eid}/timeline").status_code == 200
    assert client.get("/portfolio").status_code == 200
