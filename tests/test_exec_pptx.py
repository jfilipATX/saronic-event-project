"""P5-6 — Executive PowerPoint export (python-pptx, monochrome, <=10 slides).

The deck is built FROM the same compose_playbook / run-of-show / ledger the
web views use, so it can never disagree with the on-screen playbook. Content is
a coherent executive summary (title, overview, decisions, venue, run of show,
attendance/access, spend) — not a dump of every screen.

Acceptance checks pinned here (mirrors Edye's spec):
1. Export returns a valid .pptx (openable) with 7–10 slides.
2. Title slide shows event name + owner + location (city, ST, country).
3. Each playbook decision appears with its chosen option + reasoning.
4. Run-of-show segments appear grouped by day with owner names.
5. A double-booked owner is flagged in amber text on the ROS slide.
6. Spend line shows the ledger value (or "none — ran offline").
7. Missing venue/owner renders "Not set", not a 500.
8. Monochrome only — no shape fill uses signal-blue; wordmark/ink+neutral
   palette only. (We assert no RGB fill equals the SIGNAL blue tuple.)
"""
from __future__ import annotations

import io
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event
from app.features import exec_pptx as pptx
from app.features.visuals import SIGNAL

SIGNAL_RGB = (76, 159, 216)


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    repo.apply_migrations(c)
    return c


@pytest.fixture()
def event(conn):
    return repo.create_event(conn, Event(
        name="Fleet Week", city="Austin", state="TX", country="US",
        event_type="Naval expo", audience_estimate=2000,
        owner_name="Lt. Cmdr. Reyes", owner_role="Event Lead"))


def _build(conn, event_id, segments=None, owner_id=None):
    pb = pptx.build_playbook(conn, event_id)
    if segments is None:
        segments = []
    return pptx.build_exec_pptx(conn, event_id, pb, segments,
                                 owner_id=owner_id)


class TestExportValidity:
    def test_returns_openable_pptx_within_slide_range(self, conn, event):
        blob = _build(conn, event)
        assert blob is not None and len(blob) > 0
        # A pptx is a zip; assert it opens and has slide parts.
        zf = zipfile.ZipFile(io.BytesIO(blob))
        slides = [n for n in zf.namelist() if n.startswith("ppt/slides/slide")]
        assert 7 <= len(slides) <= 10, f"slide count {len(slides)} not in 7-10"


class TestContent:
    def test_title_slide_has_name_owner_location(self, conn, event):
        blob = _build(conn, event)
        text = pptx.extract_text(blob)
        assert "Fleet Week" in text
        assert "Lt. Cmdr. Reyes" in text
        assert "Austin" in text and "US" in text

    def test_missing_owner_renders_not_set_not_crash(self, conn):
        eid = repo.create_event(conn, Event(name="Bare", city="Boston",
                                            country="US"))
        blob = _build(conn, eid)  # no owner, no decisions, no segments
        text = pptx.extract_text(blob)
        assert "Bare" in text
        # "Not set" or a dash for the missing owner — never a 500.
        assert ("Not set" in text or "—" in text)

    def test_decisions_show_chosen_and_reasoning(self, conn, event):
        # Record a settled decision so a section exists.
        repo.record_decision(conn, repo.Decision(
            event_id=event, step="venue", question="Which venue?",
            chosen_key="venue_port_alpha",
            options=[repo.DecisionOption(
                key="venue_port_alpha", label="Port Alpha",
                reasoning="Fits 1,200 cap.")]))
        blob = _build(conn, event)
        text = pptx.extract_text(blob)
        assert "Port Alpha" in text
        assert "Fits 1,200 cap" in text or "1,200" in text

    def test_run_of_show_grouped_by_day_with_owners(self, conn, event):
        pid = repo.add_person(conn, repo.Person(name="Dana", role="Lead"))
        repo.assign_staff(conn, event, pid, role="Lead", can_check_in=True)
        seg = repo.Segment(event_id=event, title="Expo booth",
                           start="2026-03-14 10:00", end="2026-03-14 12:00",
                           track="Expo", owner_ids=[pid])
        sid = repo.add_segment(conn, seg)
        segments = repo.list_segments(conn, event)
        blob = _build(conn, event, segments=segments)
        text = pptx.extract_text(blob)
        assert "Expo booth" in text
        assert "Dana" in text

    def test_double_booked_owner_flagged_amber(self, conn, event):
        pid = repo.add_person(conn, repo.Person(name="Sam"))
        repo.assign_staff(conn, event, pid, role="Ops")
        # Two overlapping segments owned by Sam.
        s1 = repo.Segment(event_id=event, title="Briefing",
                          start="2026-03-14 10:00", end="2026-03-14 11:00",
                          track="Ops", owner_ids=[pid])
        s2 = repo.Segment(event_id=event, title="Walkthrough",
                          start="2026-03-14 10:30", end="2026-03-14 11:30",
                          track="Ops", owner_ids=[pid])
        repo.add_segment(conn, s1)
        repo.add_segment(conn, s2)
        segments = repo.list_segments(conn, event)
        prs = pptx.build_exec_pptx_obj(conn, event,
                                        pptx.build_playbook(conn, event),
                                        segments)
        # Find a run-of-show slide and check an amber run exists for the conflict.
        amber = pptx.has_amber_conflict(prs)
        assert amber, "double-booked owner should be flagged in amber"


class TestSpend:
    def test_spend_line_shows_ledger_or_offline(self, conn, event):
        blob = _build(conn, event)  # no spend recorded
        text = pptx.extract_text(blob)
        assert "none" in text.lower() and "offline" in text.lower()


class TestMonochrome:
    def test_no_signal_blue_fill_anywhere(self, conn, event):
        prs = pptx.build_exec_pptx_obj(conn, event,
                                        pptx.build_playbook(conn, event), [])
        fills = pptx.collect_fills(prs)
        for fill in fills:
            # RGB fills must never be signal-blue.
            assert fill != SIGNAL_RGB, f"signal-blue fill found: {fill}"


class TestExportRoute:
    def test_get_export_returns_pptx_download(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        import tempfile, os
        d = tempfile.mkdtemp()
        os.environ["DB_PATH"] = os.path.join(d, "t.db")
        try:
            client = TestClient(create_app())
            r = client.post("/events", data={"name": "Fleet Week",
                                            "city": "Austin", "state": "TX",
                                            "country": "US"},
                            follow_redirects=False)
            eid = int(r.headers["location"].rstrip("/").split("/")[2])
            resp = client.get(f"/events/{eid}/slides/export.pptx")
            assert resp.status_code == 200, resp.status_code
            assert "presentationml.presentation" in resp.headers.get(
                "content-type", "")
            assert resp.content[:2] == b"PK", "not a zip/pptx"
            assert "fleet-week-executive-summary.pptx" in resp.headers.get(
                "content-disposition", "")
        finally:
            os.environ.pop("DB_PATH", None)


class TestFontEmbedding:
    """H1 — the deck must carry the real brand fonts as embedded OOXML parts,
    not just name them. A font-less recipient machine must resolve the actual
    glyphs rather than silently substituting. This is the gap Edye caught:
    asserting `.font.name == 'Archivo Expanded'` passes even with no embedding.
    """

    def test_archivo_expanded_embedded_with_matching_bytes(self, conn, event):
        import hashlib, zipfile, io
        from pathlib import Path
        blob = pptx.build_exec_pptx(conn, event, pptx.build_playbook(conn, event), [])
        zf = zipfile.ZipFile(io.BytesIO(blob))
        font_parts = [n for n in zf.namelist() if n.startswith("ppt/fonts/")
                      and n.lower().endswith(".ttf")]
        assert font_parts, "no embedded font part under ppt/fonts/"

        asset = Path(__file__).resolve().parent.parent / \
            "assets/fonts/ArchivoExpanded-Bold.ttf"
        expected = hashlib.sha256(asset.read_bytes()).hexdigest()
        embedded = False
        for part in font_parts:
            digest = hashlib.sha256(zf.read(part)).hexdigest()
            if digest == expected:
                embedded = True
        assert embedded, "embedded font bytes do not match ArchivoExpanded-Bold.ttf"

    def test_theme_references_embedded_font(self, conn, event):
        import zipfile, io
        blob = pptx.build_exec_pptx(conn, event, pptx.build_playbook(conn, event), [])
        zf = zipfile.ZipFile(io.BytesIO(blob))
        theme = zf.read("ppt/theme/theme1.xml").decode("utf-8")
        assert "embeddedFont" in theme, "theme has no <a:embeddedFont> mapping"
        assert "Archivo Expanded" in theme

