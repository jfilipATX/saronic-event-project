"""SQLite repository — thin CRUD over the schema, stdlib ``sqlite3`` only.

Designed to work against an in-memory database in tests (pass ``:memory:``) and a
file in production. All PII (attendee names/emails) lives here, server-side — never
exposed in QR payloads or to the frontend.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator, List, Optional

from app.db.models import Attendee, Event, EventVariable
from app.db import schema_sql_text as _sql


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = "events.db") -> None:
    with _connect(db_path) as conn:
        conn.executescript(_sql.SCHEMA)


def create_event(conn: sqlite3.Connection, event: Event) -> int:
    cur = conn.execute(
        "INSERT INTO events (name, city, audience_estimate, event_type) "
        "VALUES (?,?,?,?)",
        (event.name, event.city, event.audience_estimate, event.event_type),
    )
    return int(cur.lastrowid)


def get_event(conn: sqlite3.Connection, event_id: int) -> Optional[Event]:
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    return Event(**dict(row)) if row else None


def list_events(conn: sqlite3.Connection) -> List[Event]:
    return [Event(**dict(r)) for r in conn.execute("SELECT * FROM events").fetchall()]


def add_attendee(conn: sqlite3.Connection, attendee: Attendee) -> int:
    cur = conn.execute(
        "INSERT INTO attendees (event_id, full_name, email, checkin_code, "
        "self_reported) VALUES (?,?,?,?,?)",
        (attendee.event_id, attendee.full_name, attendee.email,
         attendee.checkin_code, int(bool(attendee.self_reported))),
    )
    return int(cur.lastrowid)


def get_attendee_by_code(conn: sqlite3.Connection, checkin_code: str) -> Optional[Attendee]:
    row = conn.execute(
        "SELECT * FROM attendees WHERE checkin_code=?", (checkin_code,)
    ).fetchone()
    return Attendee(**dict(row)) if row else None


def mark_attended(conn: sqlite3.Connection, attendee_id: int, when: str) -> None:
    conn.execute(
        "UPDATE attendees SET attended_at=? WHERE id=? AND attended_at IS NULL",
        (when, attendee_id),
    )


def list_attendees(conn: sqlite3.Connection, event_id: int) -> List[Attendee]:
    return [
        Attendee(**dict(r))
        for r in conn.execute(
            "SELECT * FROM attendees WHERE event_id=?", (event_id,)
        ).fetchall()
    ]


def add_variable(conn: sqlite3.Connection, var: EventVariable) -> int:
    cur = conn.execute(
        "INSERT INTO event_variables (event_id, kind, value, notes) VALUES (?,?,?,?)",
        (var.event_id, var.kind, var.value, var.notes),
    )
    return int(cur.lastrowid)


def list_variables(conn: sqlite3.Connection, event_id: int) -> List[EventVariable]:
    return [
        EventVariable(**dict(r))
        for r in conn.execute(
            "SELECT * FROM event_variables WHERE event_id=?", (event_id,)
        ).fetchall()
    ]
