"""P9 — widen Concierge coverage to the rest of the decision chain.

Phase 8 shipped venue + run-of-show. The full staged chain is
event_type -> audience -> venue (slides/checkin are display-only, not staged),
plus event-level variables. This pins that the Concierge can intake/edit
event_type, audience, and variables via natural language, reusing the SAME
workflow + variable machinery the forms use (single source of truth).
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo
from app.features.concierge import ConciergeSession


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, *, system, prompt, max_tokens=1024, temperature=0.3,
                 event_id=None, surface=""):
        return self.payload


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(__import__("app.db.schema_sql_text",
                                fromlist=["SCHEMA"]).SCHEMA)
    repo.apply_migrations(c)
    return c


@pytest.fixture()
def event_id(conn):
    from app.features.workflow import CoordinatorWorkflow
    return CoordinatorWorkflow(conn).start_event(
        "Test Expo", city="San Diego", state="CA", country="US")


def test_event_type_add_via_session(conn, event_id):
    client = _FakeClient('{"scope":"event_type","action":"add","key":"convention"}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("It's a convention", client=client, conn=conn)
    assert "convention" in reply.text.lower() or "decision" in reply.text.lower()
    d = repo.get_decision(conn, repo.current_decisions(conn, event_id)[0].id) \
        if False else None
    # The staged event_type step must now be answered with the convention key.
    decided = [d for d in repo.current_decisions(conn, event_id)
               if d.step == "event_type" and not d.is_pending]
    assert decided, "event_type decision should be recorded"
    assert decided[0].chosen_key == "convention"


def test_audience_custom_value_via_session(conn, event_id):
    client = _FakeClient('{"scope":"audience","action":"add","key":"custom",'
                         '"value":500}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("Plan for 500 people", client=client, conn=conn)
    decided = [d for d in repo.current_decisions(conn, event_id)
               if d.step == "audience" and not d.is_pending]
    assert decided, "audience decision should be recorded"
    assert decided[0].chosen_value == "500"
    ev = repo.get_event(conn, event_id)
    assert ev.audience_estimate == 500


def test_variables_add_via_session(conn, event_id):
    client = _FakeClient('{"scope":"variables","action":"add","kind":"Hotel block",'
                         '"value":"Marriott, cutoff May 1","notes":"Book early"}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("Add variable hotel block Marriott cutoff May 1",
                  client=client, conn=conn)
    vars_ = repo.list_variables(conn, event_id)
    assert any(v.kind == "Hotel block" and "Marriott" in v.value for v in vars_)


def test_event_type_edit_revises_existing(conn, event_id):
    # First set event_type, then change it via NL edit.
    from app.features.workflow import CoordinatorWorkflow
    CoordinatorWorkflow(conn).choose(event_id, "event_type", "panel")
    client = _FakeClient('{"scope":"event_type","action":"edit","field":"key",'
                         '"value":"convention"}')
    s = ConciergeSession(event_id=event_id)
    reply = s.ask("change event type to convention", client=client, conn=conn)
    decided = [d for d in repo.current_decisions(conn, event_id)
               if d.step == "event_type" and not d.is_pending]
    assert decided[-1].chosen_key == "convention"
