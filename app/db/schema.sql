"""SQLite DDL for the event planning tool.

T4 schema. Runs idempotently (IF NOT EXISTS) so the app bootstraps itself.
"""
CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    city          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    -- Feature #2 output (audience estimate) and #3 output (event type).
    audience_estimate INTEGER,
    event_type    TEXT
);
"""

CREATE_ATTENDEES = """
CREATE TABLE IF NOT EXISTS attendees (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL REFERENCES events(id),
    full_name       TEXT,
    email           TEXT,
    -- Feature #6 (QR check-in, Option A):
    checkin_code    TEXT,        -- opaque HMAC-signed token (invite_id.issued_at.HMAC)
    attended_at     TEXT,        -- set on first valid scan; NULL = no-show
    self_reported   INTEGER NOT NULL DEFAULT 0,  -- 1 if walk-in self-added (no token)
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_EVENT_VARIABLES = """
CREATE TABLE IF NOT EXISTS event_variables (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL REFERENCES events(id),
    kind          TEXT NOT NULL,   -- 'vip' | 'security_clearance' | 'speaker' | ...
    value         TEXT NOT NULL,
    notes         TEXT
);
"""
