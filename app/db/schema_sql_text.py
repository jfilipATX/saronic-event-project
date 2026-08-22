"""Inline copy of the schema DDL so ``repository.py`` needs no file IO (test-friendly)."""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    city          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    audience_estimate INTEGER,
    event_type    TEXT
);

CREATE TABLE IF NOT EXISTS attendees (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL,
    full_name       TEXT,
    email           TEXT,
    checkin_code    TEXT,
    attended_at     TEXT,
    self_reported   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    withdrawn_at    TEXT,
    erased_at       TEXT
);

CREATE TABLE IF NOT EXISTS event_variables (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    value         TEXT NOT NULL,
    notes         TEXT
);

-- T11.5: append-only decision log. A revision inserts a NEW row and points the
-- old row at it via superseded_by, so history is never destroyed.
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL,
    step          TEXT NOT NULL,
    question      TEXT NOT NULL,
    options_json  TEXT NOT NULL,
    chosen_key    TEXT,
    chosen_value  TEXT,
    decided_by    TEXT,
    decided_at    TEXT,
    note          TEXT,
    superseded_by INTEGER REFERENCES decisions(id),
    blocked_reason TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_decisions_event ON decisions(event_id, id);

-- P2-3: venue favourites and used-before history, keyed on a stable venue_ref
-- rather than the display name so a rename does not orphan the record.
CREATE TABLE IF NOT EXISTS venue_favourites (
    venue_ref  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS venue_uses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_ref  TEXT NOT NULL,
    event_id   INTEGER,
    event_name TEXT NOT NULL,
    used_on    TEXT,
    notes      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_venue_uses_ref ON venue_uses(venue_ref, used_on);
"""
