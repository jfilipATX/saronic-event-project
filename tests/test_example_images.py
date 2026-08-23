"""P6-4 — example imagery for the demo / quick starts.

The repo bundles brand-correct example images (assets/images/example/). Seeding
copies them into an event's library as origin='example', idempotently, so a
loaded demo (or coordinator) has something to compose against without network.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.db import repository as repo
from app.features.example_images import seed_example_images, example_image_dir
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


def test_seed_adds_example_images_as_origin_example():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    added = seed_example_images(c, eid, os.path.join("generated", "visuals", str(eid)))
    assert added >= 1
    imgs = repo.library_images(c, eid)
    assert any(im.origin == "example" for im in imgs)
    # files actually copied next to the library
    assert any(os.path.exists(im.path) for im in imgs)


def test_seed_is_idempotent():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    vdir = os.path.join("generated", "visuals", str(eid))
    first = seed_example_images(c, eid, vdir)
    second = seed_example_images(c, eid, vdir)
    assert first >= 1 and second == 0  # no duplicates on re-seed


def test_seed_button_route_populates_library():
    client = _client_db()
    eid = _make(client)
    resp = client.post(f"/events/{eid}/visuals/library/seed-examples",
                       follow_redirects=False)
    assert resp.status_code == 303
    c = _direct()
    assert any(im.origin == "example" for im in repo.library_images(c, eid))
    # gallery renders the example tile label
    assert "Example image (bundled)" in client.get(f"/events/{eid}/visuals").text
