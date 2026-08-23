"""P8-1 — Concierge natural-language intake tests.

Drives venue + run-of-show extraction and edits through ConciergeSession.
Mock mode (no client) exercises the deterministic fallback + the apply paths;
the real-client path is exercised by stubbing a fake client returning JSON.
"""

import sqlite3

import pytest

from app.db import repository as repo
from app.features.concierge import ConciergeSession, _parse_json
from app.features.schedule import DayWindow


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(__import__("app.db.schema_sql_text", fromlist=["SCHEMA"]).SCHEMA)
    repo.apply_migrations(c)
    return c


@pytest.fixture()
def event_id(conn):
    return repo.create_event(conn, repo.Event(
        name="Test Expo", city="San Diego", state="CA", country="US",
        event_type="Expo"))


class _FakeClient:
    """Returns a canned JSON extraction regardless of prompt (test control)."""
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *, system, prompt, max_tokens=1024, temperature=0.3,
                 event_id=None, surface=""):
        self.calls += 1
        return self.payload


def test_parse_json_handles_fenced_and_bare():
    assert _parse_json('{"a":1}') == {"a": 1}
    assert _parse_json('```json\n{"a":1}\n```') == {"a": 1}
    assert _parse_json("no json here") is None


def test_mock_mode_without_client_is_honest(conn, event_id):
    s = ConciergeSession(event_id=event_id)
    # No client -> heuristic only; an unrecognised phrase gets an honest note.
    reply = s.ask("tell me about catering options", client=None, conn=conn)
    assert "model" in reply.text.lower() or "Claude" in reply.text


def test_venue_add_via_session(conn, event_id):
    client = _FakeClient('{"scope":"venue","action":"add","name":"Port Alpha",'
                         '"city":"San Diego","capacity":1200,'
                         '"amenities":["catering","security"]}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("Add venue Port Alpha, capacity 1200, catering + security",
                  client=client, conn=conn)
    assert "Port Alpha" in reply.text
    venues = repo.custom_venues(conn, event_id)
    assert any("Port Alpha" in v.name for v in venues)


def test_run_of_show_segment_add(conn, event_id):
    client = _FakeClient('{"scope":"run_of_show","action":"add","title":'
                         '"Doors open","start":"09:00","end":"10:00",'
                         '"track":"Program","kind":"program","owners":["Sam"]}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("Add Doors open 9 to 10, Program", client=client, conn=conn)
    assert "Doors open" in reply.text
    segs = repo.list_segments(conn, event_id)
    assert any(x.title == "Doors open" for x in segs)


def test_free_form_edit_moves_doors(conn, event_id):
    # seed a segment
    repo.add_segment(conn, repo.Segment(event_id=event_id, title="Doors open",
                                        start="2026-09-01 09:00", end="2026-09-01 11:30",
                                        track="Program", kind="program"))
    client = _FakeClient('{"scope":"run_of_show","action":"edit",'
                         '"match_title":"doors","field":"start","value":"11:00"}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("move doors open to 11am", client=client, conn=conn)
    assert "11:00" in reply.text
    segs = repo.list_segments(conn, event_id)
    hit = next(x for x in segs if "doors" in x.title.lower())
    assert hit.start.endswith("11:00")


def test_out_of_scope_returns_note(conn, event_id):
    client = _FakeClient('{"scope":null,"action":"out_of_scope",'
                         '"note":"Try the forms for that."}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("set the audience size to 5000", client=client, conn=conn)
    assert "outside" in reply.text.lower() or "only" in reply.text.lower() or "forms" in reply.text.lower()


def test_edit_unmatched_segment_is_honest(conn, event_id):
    client = _FakeClient('{"scope":"run_of_show","action":"edit",'
                         '"match_title":"nonexistent","field":"start","value":"11:00"}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("move the gala to 11am", client=client, conn=conn)
    assert "couldn't find" in reply.text.lower()
