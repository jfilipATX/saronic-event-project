"""P5-5 — staff as first-class entities (a global people pool).

Previously staff were a per-event table; segments referenced them by id. The
backing table is now `people` (event_id nullable) with an `event_staff` join
for event-specific assignment. People added on the People screen are global; an
event can attach any existing person, and that person's id is stable across
events so a segment owner reference means the same human everywhere.

The interesting invariants, pinned here:

* **A person exists once in the pool, not per event.** Adding Dana on the
  People screen then attaching her to two events must not create two Danas.
* **The id is stable across events**, because segments store owner ids and a
  re-attached person must keep the id the segments already point at.
* **The migration preserves existing segment owner references.** A database
  with the old `staff` table must upgrade without orphaning any segment's
  owners — the rows keep their ids.
* **An event-specific-only person is not shared**, matching the request that
  some staff are event-scoped and others are reusable.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db.models import Event, Person


@pytest.fixture()
def conn():
    import app.db.repository as repo
    from app.db import schema_sql_text as sql

    handle = sqlite3.connect(":memory:")
    handle.row_factory = sqlite3.Row
    handle.executescript(sql.SCHEMA)
    repo.apply_migrations(handle)
    return handle


@pytest.fixture()
def event(conn):
    import app.db.repository as repo

    return repo.create_event(conn, Event(name="Fleet Week", city="Austin"))


def _two_events(conn):
    import app.db.repository as repo
    from app.db.models import Event

    a = repo.create_event(conn, Event(name="Fleet Week", city="Austin"))
    b = repo.create_event(conn, Event(name="Expo", city="San Diego"))
    return a, b


class TestPeoplePool:
    def test_a_person_is_global_not_per_event(self, conn, event):
        import app.db.repository as repo

        pid = repo.add_person(conn, Person(name="Dana Reyes", role="Lead"))
        repo.assign_staff(conn, event, pid)
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        repo.assign_staff(conn, other, pid)
        assert [p.id for p in repo.list_people(conn)] == [pid]
        assert repo.event_staff(conn, event) == [pid]
        assert repo.event_staff(conn, other) == [pid]

    def test_event_specific_staff_are_not_shared(self, conn, event):
        import app.db.repository as repo

        a, b = _two_events(conn)
        only_a = repo.add_person(conn, Person(name="Sam", role="A/V"))
        repo.assign_staff(conn, a, only_a)
        assert repo.event_staff(conn, b) == []
        assert [p.id for p in repo.list_people(conn)] == [only_a]

    def test_a_person_needs_a_name(self, conn, event):
        import app.db.repository as repo
        from app.db.models import Person

        with pytest.raises(ValueError, match="name"):
            repo.add_person(conn, Person(name="  ", role="X"))

    def test_erasure_is_global_and_keeps_the_record(self, conn, event):
        """Erasure anonymises the pool entry, not a per-event copy."""
        import app.db.repository as repo

        pid = repo.add_person(conn, Person(name="Dana", role="Lead"))
        repo.erase_person(conn, pid)
        live = [p for p in repo.list_people(conn, include_erased=True)
                if not p.is_erased]
        assert live == []
        assert repo.get_person(conn, pid).is_erased

    def test_segments_reference_the_global_pool(self, conn, event):
        """A segment owner id is a person id, resolvable from the pool."""
        import app.db.repository as repo
        from app.db.models import Person, Segment

        pid = repo.add_person(conn, Person(name="Dana", role="Lead"))
        repo.assign_staff(conn, event, pid)
        seg = Segment(event_id=event, title="Doors", start="2026-03-14T09:00",
                      end="2026-03-14T10:00", owner_ids=[pid])
        repo.add_segment(conn, seg)
        got = repo.list_segments(conn, event)[0]
        assert got.owner_ids == [pid]
        assert repo.get_person(conn, pid).display_name == "Dana"


class TestStaffMigration:
    """The old `staff` table must upgrade without orphaning segment owners."""

    def _old_db(self):
        handle = sqlite3.connect(":memory:")
        handle.row_factory = sqlite3.Row
        # A pre-P5-5 database: it HAS the (now-legacy) staff table, plus the
        # people/event_staff tables SCHEMA already created on every open — the
        # migration only promotes data, it does not create tables.
        handle.executescript("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, city TEXT,
                audience_estimate INTEGER, event_type TEXT);
            CREATE TABLE people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, role TEXT, erased_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
            CREATE TABLE event_staff (
                event_id INTEGER NOT NULL, person_id INTEGER NOT NULL,
                role TEXT, can_check_in INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (event_id, person_id));
            CREATE TABLE staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL, name TEXT, role TEXT,
                erased_at TEXT, created_at TEXT);
            CREATE TABLE segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL, title TEXT, start TEXT, end TEXT,
                track TEXT, kind TEXT, location TEXT, notes TEXT,
                owners_json TEXT);
        """)
        return handle

    def test_existing_staff_become_global_people_with_stable_ids(self):
        import app.db.repository as repo

        db = self._old_db()
        db.execute("INSERT INTO events (name, city) VALUES ('FW', 'Austin')")
        eid = 1
        db.execute("INSERT INTO staff (event_id, name, role) VALUES (?,?,?)",
                   (eid, "Dana Reyes", "Booth lead"))
        db.execute("INSERT INTO segments (event_id, title, start, end, owners_json)"
                   " VALUES (?, 'Doors', '2026-03-14T09:00', '2026-03-14T10:00',"
                   " '[1]')", (eid,))
        db.commit()
        repo.migrate_staff_to_people(db)

        # The person keeps id 1, so the segment still resolves to her.
        assert repo.get_person(db, 1).name == "Dana Reyes"
        assert repo.event_staff(db, eid) == [1]
        seg = repo.list_segments(db, eid)[0]
        assert seg.owner_ids == [1]
        assert [p.id for p in repo.list_people(db)] == [1]

    def test_migration_is_idempotent(self):
        import app.db.repository as repo

        db = self._old_db()
        db.execute("INSERT INTO events (name, city) VALUES ('FW', 'Austin')")
        db.execute("INSERT INTO staff (event_id, name, role) VALUES (1, 'Dana', 'L')")
        db.commit()
        repo.migrate_staff_to_people(db)
        repo.migrate_staff_to_people(db)
        assert [p.id for p in repo.list_people(db)] == [1]
