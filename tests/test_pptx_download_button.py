"""P6-2 — the exec PPTX must be downloadable from the UI.

The export route exists (GET /events/{id}/slides/export.pptx) but the slides
view only linked the Markdown outline — so the feature was unreachable. This
pins the download button onto the slides view.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _client():
    import os
    import tempfile
    d = tempfile.mkdtemp()
    os.environ["DB_PATH"] = os.path.join(d, "t.db")
    return TestClient(create_app())


def _make_event(client):
    r = client.post("/events", data={"name": "Fleet Week", "city": "Austin",
                                     "state": "TX", "country": "US"},
                    follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


def test_slides_view_links_pptx_download():
    client = _client()
    eid = _make_event(client)
    html = client.get(f"/events/{eid}/slides").text
    # A primary download button pointing at the .pptx export route.
    assert "slides/export.pptx" in html
    assert "Download PowerPoint" in html


def test_pptx_download_button_reaches_valid_export():
    client = _client()
    eid = _make_event(client)
    resp = client.get(f"/events/{eid}/slides/export.pptx")
    assert resp.status_code == 200
    assert "presentationml.presentation" in resp.headers.get("content-type", "")
