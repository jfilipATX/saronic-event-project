"""Inline copy of the schema DDL so ``repository.py`` needs no file IO (test-friendly)."""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    city          TEXT,
    state         TEXT,
    country       TEXT NOT NULL DEFAULT 'US',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    audience_estimate INTEGER,
    event_type    TEXT,
    starts_at     TEXT,
    ends_at       TEXT,
    owner_name    TEXT,
    owner_role    TEXT
);

-- P5-2: flexible / multi-day dates. An event is one or more calendar days,
-- each with an optional open/close hour (times optional while scoping). The
-- legacy starts_at/ends_at columns stay populated for backward compatibility
-- (board/playbook/slides read a window), derived from day 1 / last day.
CREATE TABLE IF NOT EXISTS event_days (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    day_index INTEGER NOT NULL,
    date     TEXT NOT NULL,
    open     TEXT,
    close    TEXT
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
    erased_at       TEXT,
    title           TEXT,
    company         TEXT,
    is_vip          INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS library_images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL,
    path          TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    article_title TEXT,
    article_url   TEXT,
    origin        TEXT NOT NULL DEFAULT 'uploaded',
    backdrop_kind TEXT NOT NULL DEFAULT 'unknown',
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (event_id, source_url)
);

CREATE INDEX IF NOT EXISTS idx_library_event ON library_images(event_id);

CREATE TABLE IF NOT EXISTS people (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT,
    role       TEXT,
    erased_at  TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_people_name ON people(name);

-- Per-event membership of the global people pool (P5-9/P5-5). A person is
-- global; this join records which events they are staffed on, an optional
-- local role title override, and can_check_in — the per-event capability to
-- run the check-in view. can_check_in is deliberately NOT on people: a person
-- granted check-in on Event A must not be implicitly check-in on Event B.
CREATE TABLE IF NOT EXISTS event_staff (
    event_id     INTEGER NOT NULL,
    person_id    INTEGER NOT NULL,
    role         TEXT,
    can_check_in INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (event_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_event_staff_event ON event_staff(event_id);
CREATE INDEX IF NOT EXISTS idx_event_staff_person ON event_staff(person_id);

CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER NOT NULL,
    title      TEXT NOT NULL,
    start      TEXT NOT NULL,
    end        TEXT NOT NULL,
    track      TEXT NOT NULL DEFAULT 'Logistics',
    kind       TEXT NOT NULL DEFAULT 'logistics',
    location   TEXT,
    notes      TEXT,
    owners_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_segments_event ON segments(event_id, start);

CREATE TABLE IF NOT EXISTS custom_venues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER NOT NULL,
    venue_ref  TEXT NOT NULL,
    name       TEXT NOT NULL,
    city       TEXT,
    capacity   INTEGER NOT NULL,
    website    TEXT,
    notes      TEXT,
    source_url TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_custom_venues_event ON custom_venues(event_id);

CREATE TABLE IF NOT EXISTS spend_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER,
    surface       TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    usd           REAL NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_spend_event ON spend_log(event_id, id);

CREATE TABLE IF NOT EXISTS vip_alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL,
    attendee_id   INTEGER,
    attendee_name TEXT NOT NULL,
    company       TEXT,
    arrived_at    TEXT,
    delivered     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_venue_uses_ref ON venue_uses(venue_ref, used_on);
"""
