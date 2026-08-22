"""SQLite repository — thin CRUD over the schema, stdlib ``sqlite3`` only.

Designed to work against an in-memory database in tests (pass ``:memory:``) and a
file in production. All PII (attendee names/emails) lives here, server-side — never
exposed in QR payloads or to the frontend.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, List, Optional

from app.db.models import Attendee, Decision, DecisionOption, Event, EventVariable
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
        apply_migrations(conn)


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


def _row_to_attendee(row: sqlite3.Row) -> Attendee:
    """Hydrate an attendee, restoring types SQLite cannot represent.

    SQLite has no boolean type: ``self_reported`` goes in as 1/0 and comes back
    as an int. Leaving it as an int makes every ``is True`` check silently fail
    and lets a walk-in be mistaken for a verified attendee, so the conversion
    happens here, once, rather than at each call site.
    """
    data = dict(row)
    data["self_reported"] = bool(data.get("self_reported", 0))
    return Attendee(**data)


def get_attendee_by_code(conn: sqlite3.Connection, checkin_code: str) -> Optional[Attendee]:
    row = conn.execute(
        "SELECT * FROM attendees WHERE checkin_code=?", (checkin_code,)
    ).fetchone()
    return _row_to_attendee(row) if row else None


def mark_attended(conn: sqlite3.Connection, attendee_id: int, when: str) -> None:
    conn.execute(
        "UPDATE attendees SET attended_at=? WHERE id=? AND attended_at IS NULL",
        (when, attendee_id),
    )


def list_attendees(conn: sqlite3.Connection, event_id: int) -> List[Attendee]:
    return [
        _row_to_attendee(r)
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


# ─────────────────────────────────────────────────────────────────────────────
# T11.5 — decision log
#
# Append-only by design. ``record_decision`` inserts; ``revise_decision`` inserts
# a successor and marks the predecessor superseded. Nothing is ever UPDATEd away,
# so the coordinator can always answer "why did we change the venue?".
# ─────────────────────────────────────────────────────────────────────────────

_DECISION_COLUMNS = (
    "id, event_id, step, question, options_json, chosen_key, chosen_value, "
    "decided_by, decided_at, note, superseded_by, blocked_reason"
)

#: Columns added after the first release, as (table, column, DDL type). The schema
#: is CREATE TABLE IF NOT EXISTS, which does NOT add columns to a database that
#: already exists — so an existing events.db would silently lack these and every
#: query naming them would fail. Applied idempotently on every init_db().
_ADDED_COLUMNS = (
    ("decisions", "blocked_reason", "TEXT"),
    ("decisions", "chosen_value", "TEXT"),
)


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Add columns missing from an older database. Returns what was applied."""
    applied: list[str] = []
    for table, column, ddl in _ADDED_COLUMNS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:            # table absent entirely; schema will create it
            continue
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            applied.append(f"{table}.{column}")
    return applied


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _dump_options(options: List[DecisionOption]) -> str:
    return json.dumps(
        [
            {"key": o.key, "label": o.label, "reasoning": o.reasoning, "data": o.data}
            for o in options
        ]
    )


def _load_options(raw: str) -> List[DecisionOption]:
    return [
        DecisionOption(
            key=o["key"],
            label=o["label"],
            reasoning=o.get("reasoning", ""),
            data=o.get("data") or {},
        )
        for o in json.loads(raw)
    ]


def _row_to_decision(row: sqlite3.Row) -> Decision:
    return Decision(
        id=row["id"],
        event_id=row["event_id"],
        step=row["step"],
        question=row["question"],
        options=_load_options(row["options_json"]),
        chosen_key=row["chosen_key"],
        chosen_value=row["chosen_value"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        note=row["note"],
        superseded_by=row["superseded_by"],
        blocked_reason=row["blocked_reason"],
    )


def _resolved_value(decision: Decision) -> Optional[str]:
    """The value to persist for the chosen option.

    Only options that declare ``data["requires_value"]`` may carry one. A stray
    value on a preset option is dropped rather than stored, so the audit trail
    cannot accumulate values that never influenced anything.
    """
    chosen = decision.chosen_option
    if chosen is None or not chosen.data.get("requires_value"):
        return None
    return decision.chosen_value


def _validate(decision: Decision) -> None:
    if not decision.options and not decision.is_blocked:
        raise ValueError(
            "A decision must offer at least one option to the coordinator. "
            "If the tool genuinely has nothing to offer, set blocked_reason to "
            "explain why — an empty slate is information, not a failure."
        )
    if decision.chosen_key is not None:
        keys = {o.key for o in decision.options}
        if decision.chosen_key not in keys:
            raise ValueError(
                f"chosen_key {decision.chosen_key!r} is not among the offered options "
                f"({sorted(keys)}); the tool must never record a choice it never offered."
            )


def record_decision(conn: sqlite3.Connection, decision: Decision) -> int:
    """Persist a decision point. Returns the new decision id."""
    _validate(decision)
    decided_at = decision.decided_at
    if decision.chosen_key is not None and not decided_at:
        decided_at = _now()
    cur = conn.execute(
        "INSERT INTO decisions (event_id, step, question, options_json, chosen_key, "
        "chosen_value, decided_by, decided_at, note, blocked_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            decision.event_id,
            decision.step,
            decision.question,
            _dump_options(decision.options),
            decision.chosen_key,
            _resolved_value(decision),
            decision.decided_by,
            decided_at,
            decision.note,
            decision.blocked_reason,
        ),
    )
    return int(cur.lastrowid)


def get_decision(conn: sqlite3.Connection, decision_id: int) -> Optional[Decision]:
    row = conn.execute(
        f"SELECT {_DECISION_COLUMNS} FROM decisions WHERE id=?", (decision_id,)
    ).fetchone()
    return _row_to_decision(row) if row else None


def revise_decision(
    conn: sqlite3.Connection,
    decision_id: int,
    *,
    chosen_key: Optional[str] = None,
    note: Optional[str] = None,
    decided_by: Optional[str] = None,
    options: Optional[List[DecisionOption]] = None,
    chosen_value: Optional[str] = None,
) -> int:
    """Supersede ``decision_id`` with a new row carrying the revised choice.

    The original row is preserved verbatim and back-linked, so the playbook can
    show "we picked A, then moved to B because <note>".
    """
    original = get_decision(conn, decision_id)
    if original is None:
        raise LookupError(f"No decision with id {decision_id}")
    if original.superseded_by is not None:
        raise ValueError(
            f"Decision {decision_id} was already superseded by {original.superseded_by}; "
            "revise the current one instead."
        )

    successor = Decision(
        event_id=original.event_id,
        step=original.step,
        question=original.question,
        options=options if options is not None else original.options,
        chosen_key=chosen_key if chosen_key is not None else original.chosen_key,
        chosen_value=chosen_value if chosen_value is not None else original.chosen_value,
        decided_by=decided_by if decided_by is not None else original.decided_by,
        note=note,
    )
    new_id = record_decision(conn, successor)
    conn.execute("UPDATE decisions SET superseded_by=? WHERE id=?", (new_id, decision_id))
    return new_id


def current_decisions(conn: sqlite3.Connection, event_id: int) -> List[Decision]:
    """Live decisions only (one per step), in the order the coordinator made them."""
    rows = conn.execute(
        f"SELECT {_DECISION_COLUMNS} FROM decisions "
        "WHERE event_id=? AND superseded_by IS NULL ORDER BY id",
        (event_id,),
    ).fetchall()
    return [_row_to_decision(r) for r in rows]


def decision_history(conn: sqlite3.Connection, event_id: int) -> List[Decision]:
    """Every decision ever recorded for the event, oldest first — append-only audit."""
    rows = conn.execute(
        f"SELECT {_DECISION_COLUMNS} FROM decisions WHERE event_id=? ORDER BY id",
        (event_id,),
    ).fetchall()
    return [_row_to_decision(r) for r in rows]
