"""P7-1 — lifecycle controls (Complete/Archive/Delete) render side-by-side and
cohesively, not stacked.

The controls live in one `.lifecycle-actions` flex row; the per-action <form>
wrappers are `inline-flex` so they sit beside each other instead of wrapping.
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
    r = client.post("/events", data={"name": "Demo", "city": "Austin",
                                     "state": "TX", "country": "US"},
                    follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


def test_lifecycle_controls_in_one_flex_row():
    client = _client_db()
    eid = _make(client)
    html = client.get("/").text
    # All three actions share one lifecycle-actions container (side-by-side).
    assert html.count("lifecycle-actions") >= 1
    # Each action is a btn-lifecycle (consistent styling) and Delete is danger.
    assert "btn-lifecycle" in html
    assert "btn-lifecycle danger" in html
    # The CSS makes .inline-form an inline-flex item so they don't stack.
    css = client.get("/static/theme.css").text
    assert ".inline-form { display: inline-flex" in css
