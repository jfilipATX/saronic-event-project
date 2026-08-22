"""An existing database created before a schema change must keep working.

The schema is CREATE TABLE IF NOT EXISTS, which does NOT add columns to a
database that already exists. Without a migration on the app's own connection
path, anyone who had run the tool before `blocked_reason` landed gets a 500
(`no such column: blocked_reason`) on their existing events — the exact user
whose data we most need to not break.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo
from app.main import create_app

#: The decisions table exactly as it existed before blocked_reason was added.
_PRE_MIGRATION_SCHEMA = """
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, city TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    audience_estimate INTEGER, event_type TEXT);
CREATE TABLE attendees (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
    full_name TEXT, email TEXT, checkin_code TEXT, attended_at TEXT,
    self_reported INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE event_variables (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
    kind TEXT NOT NULL, value TEXT NOT NULL, notes TEXT);
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
    step TEXT NOT NULL, question TEXT NOT NULL, options_json TEXT NOT NULL,
    chosen_key TEXT, decided_by TEXT, decided_at TEXT, note TEXT,
    superseded_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
"""

_OPTIONS = ('[{"key":"convention","label":"Convention","reasoning":"r","data":{}}]')


@pytest.fixture()
def legacy_db(tmp_path) -> str:
    """A database with real data and the pre-migration schema."""
    path = str(tmp_path / "events.db")
    conn = sqlite3.connect(path)
    conn.executescript(_PRE_MIGRATION_SCHEMA)
    conn.execute("INSERT INTO events (name, city) VALUES ('Pre-existing Event','Austin')")
    conn.execute(
        "INSERT INTO decisions (event_id, step, question, options_json, chosen_key) "
        "VALUES (1,'event_type','What kind of event is this?',?,'convention')",
        (_OPTIONS,),
    )
    conn.commit()
    conn.close()
    return path


def _columns(path: str, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


class TestLegacyDatabaseIsMigratedByTheApp:
    def test_fixture_really_is_pre_migration(self, legacy_db):
        assert "blocked_reason" not in _columns(legacy_db, "decisions")

    def test_opening_the_app_migrates_the_database(self, legacy_db):
        client = TestClient(create_app(db_path=legacy_db))
        client.get("/")
        assert "blocked_reason" in _columns(legacy_db, "decisions")

    def test_existing_event_still_lists(self, legacy_db):
        client = TestClient(create_app(db_path=legacy_db))
        assert "Pre-existing Event" in client.get("/").text

    def test_playbook_on_a_legacy_event_does_not_500(self, legacy_db):
        """This is the exact crash: current_decisions() selects blocked_reason."""
        client = TestClient(create_app(db_path=legacy_db))
        r = client.get("/events/1/playbook")
        assert r.status_code == 200, r.text

    def test_legacy_decision_data_survives_the_migration(self, legacy_db):
        client = TestClient(create_app(db_path=legacy_db))
        client.get("/events/1/playbook")
        conn = sqlite3.connect(legacy_db)
        conn.row_factory = sqlite3.Row
        try:
            d = repo.current_decisions(conn, 1)
            assert [x.step for x in d] == ["event_type"]
            assert d[0].chosen_key == "convention"
            assert d[0].blocked_reason is None
        finally:
            conn.close()

    def test_the_workflow_continues_on_a_legacy_event(self, legacy_db):
        client = TestClient(create_app(db_path=legacy_db))
        r = client.post("/events/1/decide",
                        data={"step": "event_type", "key": "convention"},
                        follow_redirects=False)
        assert r.status_code in (302, 303), r.text

    def test_migration_is_idempotent_across_restarts(self, legacy_db):
        for _ in range(3):
            client = TestClient(create_app(db_path=legacy_db))
            assert client.get("/").status_code == 200
        assert "blocked_reason" in _columns(legacy_db, "decisions")
