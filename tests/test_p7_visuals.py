"""P7-6 — visuals show real Saronic product imagery + captions.

The example seed now bundles the real press-kit product shot (Corsair hero) and
every example tile carries a coordinator-facing caption, so the demo's
generated images are identifiable (not mysterious abstract blocks).
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
    r = client.post("/events", data={"name": "FW", "city": "SF", "state": "CA",
                                     "country": "US"}, follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


def _direct():
    c = sqlite3.connect(os.environ["DB_PATH"], isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def test_seed_includes_real_press_kit_product_shot():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    vdir = os.path.join("generated", "visuals", str(eid))
    seed_example_images(c, eid, vdir)
    imgs = repo.library_images(c, eid)
    # Real Saronic hardware (Corsair hero) is present, not just synthetic art.
    assert any("Corsair" in (im.caption or "") for im in imgs)
    # Every tile has a caption so it's identifiable in the sidebar.
    assert all(im.caption for im in imgs if im.origin == "example")


def test_visuals_tile_renders_caption_for_example_images():
    client = _client_db()
    eid = _make(client)
    c = _direct()
    vdir = os.path.join("generated", "visuals", str(eid))
    seed_example_images(c, eid, vdir)
    html = client.get(f"/events/{eid}/visuals").text
    assert "Saronic Corsair" in html
    assert "real product hero shot" in html
