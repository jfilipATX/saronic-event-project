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
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_variables (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    value         TEXT NOT NULL,
    notes         TEXT
);
"""
