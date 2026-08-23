"""Disoverability fix (Phase 6 follow-up).

The user couldn't find the Fleet Week demo loader or the per-event Archive/Delete
controls — they were all `btn-quiet` ghosts, near-invisible. This pins the fix:
the demo loader is a distinct, visible callout (not a quiet ghost), and the
lifecycle controls on each event row carry a findable (non-quiet) class.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.db import repository as repo
from app.main import create_app


def _client_db():
    d = tempfile.mkdtemp()
    os.environ["DB_PATH"] = os.path.join(d, "t.db")
    return TestClient(create_app())


def _make(client):
    r = client.post("/events", data={"name": "Test", "city": "Austin",
                                     "state": "TX", "country": "US"},
                    follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


def test_demo_loader_is_visible_not_quiet_ghost():
    client = _client_db()
    html = client.get("/").text
    # The loader exists...
    assert "Load Fleet Week demo" in html
    # ...and is wrapped in a distinct callout, not a bare btn-quiet ghost.
    assert "demo-callout" in html
    # The button inside the callout is NOT a quiet ghost (promoted to findable).
    callout = html[html.index("demo-callout"):html.index("demo-callout") + 400]
    assert "btn-quiet" not in callout


def test_lifecycle_controls_present_and_findable_per_event():
    client = _client_db()
    eid = _make(client)
    html = client.get("/").text
    # Lifecycle actions render for the event row.
    assert f"/events/{eid}/archive" in html
    assert f"/events/{eid}/delete" in html
    # They are not invisible quiet ghosts — promoted to a findable class.
    assert "lifecycle-actions" in html
    row = html[html.index(f"/events/{eid}/archive"):html.index(f"/events/{eid}/delete") + 80]
    assert "btn-quiet" not in row
