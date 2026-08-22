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
from typing import Dict, Iterator, List, Optional

from app.db.models import (
    Attendee, Decision, DecisionOption, Event, EventVariable, SpendEntry,
    VenueUse, VipAlert,
)
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
        "self_reported, title, company, is_vip) VALUES (?,?,?,?,?,?,?,?)",
        (attendee.event_id, attendee.full_name, attendee.email,
         attendee.checkin_code, int(bool(attendee.self_reported)),
         attendee.title, attendee.company, int(bool(attendee.is_vip))),
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
    data["is_vip"] = bool(data.get("is_vip", 0))
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


def list_attendees(conn: sqlite3.Connection, event_id: int,
                   include_withdrawn: bool = False) -> List[Attendee]:
    """The active roster by default.

    Withdrawn and erased people are excluded unless asked for: a coordinator
    counting badges wants who is actually coming, and silently including
    cancellations inflates every number downstream.
    """
    sql_text = "SELECT * FROM attendees WHERE event_id=?"
    if not include_withdrawn:
        sql_text += " AND withdrawn_at IS NULL AND erased_at IS NULL"
    return [_row_to_attendee(r)
            for r in conn.execute(sql_text, (event_id,)).fetchall()]


def get_attendee(conn: sqlite3.Connection, attendee_id: int) -> Optional[Attendee]:
    row = conn.execute("SELECT * FROM attendees WHERE id=?", (attendee_id,)).fetchone()
    return _row_to_attendee(row) if row else None


def withdraw_attendee(conn: sqlite3.Connection, attendee_id: int) -> None:
    """Cancel an invitee. Reversible, and the name is deliberately kept."""
    conn.execute(
        "UPDATE attendees SET withdrawn_at=datetime('now') "
        "WHERE id=? AND withdrawn_at IS NULL AND erased_at IS NULL",
        (attendee_id,),
    )


def reinstate_attendee(conn: sqlite3.Connection, attendee_id: int) -> None:
    person = get_attendee(conn, attendee_id)
    if person is not None and person.is_erased:
        raise ValueError(
            f"Attendee {attendee_id} was erased; erasure is irreversible by "
            "design and the personal details no longer exist."
        )
    conn.execute("UPDATE attendees SET withdrawn_at=NULL WHERE id=?", (attendee_id,))


#: What an erased row shows instead of a name. Explicit rather than blank so an
#: erasure is self-evident: a blank name could be a data bug, and a coordinator
#: seeing an empty row would reasonably try to "fix" it.
ERASED_PLACEHOLDER = "(erased at the attendee's request)"


def erase_attendee(conn: sqlite3.Connection, attendee_id: int) -> None:
    """Destroy an attendee's personal data in place. Irreversible.

    Attendance is kept as an anonymous tally — deleting the row would corrupt
    the count of who was actually in the building, which is a safety record at a
    defense event, not a marketing metric. The check-in code goes too: a
    credential tied to a person is personal data.
    """
    conn.execute(
        "UPDATE attendees SET full_name=?, email=NULL, checkin_code=NULL, "
        "erased_at=COALESCE(erased_at, datetime('now')) WHERE id=?",
        (ERASED_PLACEHOLDER, attendee_id),
    )


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
    ("attendees", "withdrawn_at", "TEXT"),
    ("attendees", "erased_at", "TEXT"),
    ("attendees", "title", "TEXT"),
    ("attendees", "company", "TEXT"),
    ("attendees", "is_vip", "INTEGER NOT NULL DEFAULT 0"),
)

#: Tables added after the first release. CREATE TABLE IF NOT EXISTS in SCHEMA
#: already handles these on open, listed here for the record.
_ADDED_TABLES = ("venue_favourites", "venue_uses", "vip_alerts")


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


# ─────────────────────────────────────────────────────────────────────────────
# P2-3 — venue favourites and used-before history
#
# Both key on a stable ``venue_ref``, never the display name: a rebranded venue
# must keep its history, and two venues sharing a name in different cities must
# never merge.
# ─────────────────────────────────────────────────────────────────────────────


def set_favourite(conn: sqlite3.Connection, venue_ref: str, on: bool = True) -> None:
    if on:
        conn.execute(
            "INSERT OR IGNORE INTO venue_favourites (venue_ref) VALUES (?)",
            (venue_ref,),
        )
    else:
        conn.execute("DELETE FROM venue_favourites WHERE venue_ref=?", (venue_ref,))


def favourites(conn: sqlite3.Connection) -> set:
    return {r["venue_ref"] for r in conn.execute(
        "SELECT venue_ref FROM venue_favourites")}


def record_venue_use(conn: sqlite3.Connection, use: VenueUse) -> int:
    cur = conn.execute(
        "INSERT INTO venue_uses (venue_ref, event_id, event_name, used_on, notes) "
        "VALUES (?,?,?,?,?)",
        (use.venue_ref, use.event_id, use.event_name, use.used_on, use.notes),
    )
    return int(cur.lastrowid)


def venue_uses(conn: sqlite3.Connection) -> Dict[str, List[VenueUse]]:
    """All recorded uses grouped by venue_ref, most recent first within each."""
    rows = conn.execute(
        "SELECT id, venue_ref, event_id, event_name, used_on, notes FROM venue_uses "
        "ORDER BY COALESCE(used_on, '') DESC, id DESC"
    ).fetchall()
    grouped: Dict[str, List[VenueUse]] = {}
    for r in rows:
        grouped.setdefault(r["venue_ref"], []).append(VenueUse(**dict(r)))
    return grouped


# ─────────────────────────────────────────────────────────────────────────────
# P2-5 — VIP arrival alerts
#
# Recorded, not sent. Until SMTP is configured, logging what WOULD be sent is
# the honest option: marking these delivered would have the coordinator believe
# a notification went out when none did.
# ─────────────────────────────────────────────────────────────────────────────


def record_vip_alert(conn: sqlite3.Connection, alert: VipAlert) -> int:
    cur = conn.execute(
        "INSERT INTO vip_alerts (event_id, attendee_id, attendee_name, company, "
        "arrived_at, delivered) VALUES (?,?,?,?,?,?)",
        (alert.event_id, alert.attendee_id, alert.attendee_name, alert.company,
         alert.arrived_at, int(bool(alert.delivered))),
    )
    return int(cur.lastrowid)


def vip_alerts(conn: sqlite3.Connection, event_id: int) -> List[VipAlert]:
    rows = conn.execute(
        "SELECT id, event_id, attendee_id, attendee_name, company, arrived_at, "
        "delivered FROM vip_alerts WHERE event_id=? ORDER BY id", (event_id,)
    ).fetchall()
    out = []
    for r in rows:
        data = dict(r)
        data["delivered"] = bool(data["delivered"])
        out.append(VipAlert(**data))
    return out


def get_attendee_by_email(conn: sqlite3.Connection, event_id: int,
                          email: str) -> Optional[Attendee]:
    """Find an invitee by email within one event.

    Erased people have no email, so they are structurally unfindable here —
    which is the correct outcome of an erasure request.
    """
    row = conn.execute(
        "SELECT * FROM attendees WHERE event_id=? AND email IS NOT NULL "
        "AND lower(email)=lower(?) AND erased_at IS NULL",
        (event_id, email.strip()),
    ).fetchone()
    return _row_to_attendee(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# P3-1 — Claude spend ledger
#
# Persistent, unlike the per-process SpendMeter. The meter enforces a cap within
# one run; this answers "what did planning this event cost?" across all of them.
# ─────────────────────────────────────────────────────────────────────────────


def record_spend(conn: sqlite3.Connection, entry: SpendEntry) -> int:
    cur = conn.execute(
        "INSERT INTO spend_log (event_id, surface, model, input_tokens, "
        "output_tokens, usd, error) VALUES (?,?,?,?,?,?,?)",
        (entry.event_id, entry.surface, entry.model, entry.input_tokens,
         entry.output_tokens, float(entry.usd), entry.error),
    )
    return int(cur.lastrowid)


def spend_entries(conn: sqlite3.Connection,
                  event_id: Optional[int] = None) -> List[SpendEntry]:
    """Ledger rows, newest first. Omit ``event_id`` for everything."""
    sql_text = ("SELECT id, event_id, surface, model, input_tokens, "
                "output_tokens, usd, error, created_at FROM spend_log")
    params: tuple = ()
    if event_id is not None:
        sql_text += " WHERE event_id=?"
        params = (event_id,)
    sql_text += " ORDER BY id DESC"
    return [SpendEntry(**dict(r)) for r in conn.execute(sql_text, params).fetchall()]


def spend_total(conn: sqlite3.Connection, event_id: Optional[int] = None) -> float:
    """Total USD. Omit ``event_id`` for the global figure, which includes
    unattributed calls — otherwise the total would not reconcile with the bill."""
    if event_id is None:
        row = conn.execute("SELECT COALESCE(SUM(usd), 0) FROM spend_log").fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(usd), 0) FROM spend_log WHERE event_id=?",
            (event_id,)).fetchone()
    return round(float(row[0]), 4)


def spend_by_surface(conn: sqlite3.Connection,
                     event_id: Optional[int] = None) -> Dict[str, float]:
    sql_text = "SELECT surface, COALESCE(SUM(usd), 0) FROM spend_log"
    params: tuple = ()
    if event_id is not None:
        sql_text += " WHERE event_id=?"
        params = (event_id,)
    sql_text += " GROUP BY surface"
    return {r[0]: round(float(r[1]), 4)
            for r in conn.execute(sql_text, params).fetchall()}
