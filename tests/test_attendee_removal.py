"""P2-5 gate — attendee removal and PII erasure.

Two operations that are NOT interchangeable:

* **withdraw** — an invitee cancelled. They drop out of counts and the roster,
  but the record that they were invited survives, because a coordinator needs to
  know who was on the list. Reversible.
* **erase** — a genuine PII deletion request. Name and email are destroyed in
  place. Any check-in record survives as an anonymous tally, because deleting
  attendance would corrupt the count of who was in the building. Irreversible.

The distinction matters most in the place it is easiest to get wrong: erasing a
person must not leave their name somewhere else. These tests hunt for exactly
that.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Attendee, Event


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def event(conn) -> int:
    return repo.create_event(conn, Event(name="Fleet Week", city="Austin"))


def _attendee(conn, event_id, name="Dana Reyes", email="dana@example.com"):
    return repo.add_attendee(conn, Attendee(
        event_id=event_id, full_name=name, email=email,
        checkin_code="code-" + name.split()[0].lower()))


class TestWithdraw:
    def test_withdrawn_attendee_leaves_the_active_roster(self, conn, event):
        aid = _attendee(conn, event)
        repo.withdraw_attendee(conn, aid)
        assert [a.id for a in repo.list_attendees(conn, event)] == []

    def test_withdrawn_attendee_is_still_in_the_full_history(self, conn, event):
        aid = _attendee(conn, event)
        repo.withdraw_attendee(conn, aid)
        everyone = repo.list_attendees(conn, event, include_withdrawn=True)
        assert [a.id for a in everyone] == [aid]
        assert everyone[0].withdrawn_at

    def test_withdrawing_keeps_the_name_on_purpose(self, conn, event):
        """A cancellation is not a privacy request — the coordinator still needs
        to know who dropped out."""
        aid = _attendee(conn, event)
        repo.withdraw_attendee(conn, aid)
        person = repo.get_attendee(conn, aid)
        assert person.full_name == "Dana Reyes"

    def test_withdraw_is_reversible(self, conn, event):
        aid = _attendee(conn, event)
        repo.withdraw_attendee(conn, aid)
        repo.reinstate_attendee(conn, aid)
        assert [a.id for a in repo.list_attendees(conn, event)] == [aid]

    def test_withdrawn_attendee_cannot_check_in(self, conn, event):
        aid = _attendee(conn, event)
        repo.withdraw_attendee(conn, aid)
        assert repo.get_attendee(conn, aid).is_withdrawn is True

    def test_withdrawing_an_unknown_id_is_a_no_op_not_a_crash(self, conn):
        repo.withdraw_attendee(conn, 9999)


class TestErase:
    def test_name_and_email_are_destroyed(self, conn, event):
        aid = _attendee(conn, event)
        repo.erase_attendee(conn, aid)
        person = repo.get_attendee(conn, aid)
        assert person.full_name != "Dana Reyes"
        assert person.email is None

    def test_the_name_is_gone_from_the_database_entirely(self, conn, event):
        """The test that actually matters: grep the whole database for the name."""
        aid = _attendee(conn, event)
        repo.erase_attendee(conn, aid)
        for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall():
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                blob = " ".join(str(v) for v in tuple(row) if v is not None)
                assert "Dana Reyes" not in blob, f"name survived in {table}"
                assert "dana@example.com" not in blob, f"email survived in {table}"

    def test_the_checkin_code_is_destroyed_too(self, conn, event):
        """A code derived from personal data is personal data."""
        aid = _attendee(conn, event)
        repo.erase_attendee(conn, aid)
        assert repo.get_attendee(conn, aid).checkin_code is None

    def test_attendance_survives_as_an_anonymous_tally(self, conn, event):
        aid = _attendee(conn, event)
        repo.mark_attended(conn, aid, "2026-03-14 09:15:00")
        repo.erase_attendee(conn, aid)
        person = repo.get_attendee(conn, aid)
        assert person.attended_at is not None
        assert person.erased_at is not None

    def test_erased_attendee_is_out_of_the_active_roster(self, conn, event):
        aid = _attendee(conn, event)
        repo.erase_attendee(conn, aid)
        assert [a.id for a in repo.list_attendees(conn, event)] == []

    def test_erasure_is_not_reversible(self, conn, event):
        aid = _attendee(conn, event)
        repo.erase_attendee(conn, aid)
        with pytest.raises(ValueError, match="erased"):
            repo.reinstate_attendee(conn, aid)

    def test_erasing_twice_is_safe(self, conn, event):
        aid = _attendee(conn, event)
        repo.erase_attendee(conn, aid)
        repo.erase_attendee(conn, aid)
        assert repo.get_attendee(conn, aid).erased_at is not None

    def test_only_the_named_person_is_affected(self, conn, event):
        target = _attendee(conn, event, "Dana Reyes", "dana@example.com")
        other = _attendee(conn, event, "Sam Okoye", "sam@example.com")
        repo.erase_attendee(conn, target)
        assert repo.get_attendee(conn, other).full_name == "Sam Okoye"
        assert repo.get_attendee(conn, other).email == "sam@example.com"

    def test_counts_reflect_the_erasure(self, conn, event):
        a = _attendee(conn, event, "Dana Reyes", "dana@example.com")
        _attendee(conn, event, "Sam Okoye", "sam@example.com")
        repo.erase_attendee(conn, a)
        assert len(repo.list_attendees(conn, event)) == 1


class TestErasureIsAuditable:
    def test_an_erased_row_is_visibly_erased_not_merely_blank(self, conn, event):
        """A blank name could be a data bug; an erasure must be self-evident."""
        aid = _attendee(conn, event)
        repo.erase_attendee(conn, aid)
        person = repo.get_attendee(conn, aid)
        assert person.is_erased is True
        assert "erased" in (person.full_name or "").lower()

    def test_the_erasure_timestamp_is_recorded(self, conn, event):
        aid = _attendee(conn, event)
        repo.erase_attendee(conn, aid)
        assert repo.get_attendee(conn, aid).erased_at


class TestLegacyDatabasesMigrate:
    def test_withdrawn_and_erased_columns_are_added(self, tmp_path):
        path = str(tmp_path / "old.db")
        c = sqlite3.connect(path)
        c.executescript(
            "CREATE TABLE attendees (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event_id INTEGER NOT NULL, full_name TEXT, email TEXT, "
            "checkin_code TEXT, attended_at TEXT, "
            "self_reported INTEGER NOT NULL DEFAULT 0, created_at TEXT);")
        c.commit()
        c.close()
        repo.init_db(path)
        c = sqlite3.connect(path)
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(attendees)")}
        finally:
            c.close()
        assert {"withdrawn_at", "erased_at"} <= cols


class TestRemovalReachesTheDoor:
    """A cancelled invitation must not still open the door.

    Found live: withdraw_attendee() removed someone from the roster and every
    count, but their QR credential still scanned as VALID — the removal was
    bookkeeping that never reached the one place it physically matters.
    """

    def _invited(self, conn, event_id, secret="test-secret"):
        from app.features.qr_checkin import mint_code

        aid = _attendee(conn, event_id)
        code = mint_code(secret, aid)
        conn.execute("UPDATE attendees SET checkin_code=? WHERE id=?", (code, aid))
        return aid, code

    def test_a_withdrawn_invitation_no_longer_scans_valid(self, conn, event):
        from app.features.qr_checkin import STATE_VALID, check_in

        aid, code = self._invited(conn, event)
        repo.withdraw_attendee(conn, aid)
        state, _ = check_in(conn, "test-secret", code)
        assert state != STATE_VALID

    def test_the_scan_says_withdrawn_rather_than_tampered(self, conn, event):
        """A cancelled guest at the desk is not a security incident — the staff
        member needs to know it was withdrawn, not suspect a forgery."""
        from app.features.qr_checkin import STATE_WITHDRAWN, check_in

        aid, code = self._invited(conn, event)
        repo.withdraw_attendee(conn, aid)
        state, _ = check_in(conn, "test-secret", code)
        assert state == STATE_WITHDRAWN

    def test_withdrawing_does_not_mark_them_attended(self, conn, event):
        from app.features.qr_checkin import check_in

        aid, code = self._invited(conn, event)
        repo.withdraw_attendee(conn, aid)
        check_in(conn, "test-secret", code)
        assert repo.get_attendee(conn, aid).attended_at is None

    def test_reinstating_makes_the_credential_work_again(self, conn, event):
        from app.features.qr_checkin import STATE_VALID, check_in

        aid, code = self._invited(conn, event)
        repo.withdraw_attendee(conn, aid)
        repo.reinstate_attendee(conn, aid)
        state, _ = check_in(conn, "test-secret", code)
        assert state == STATE_VALID

    def test_an_erased_credential_does_not_scan(self, conn, event):
        from app.features.qr_checkin import STATE_VALID, check_in

        aid, code = self._invited(conn, event)
        repo.erase_attendee(conn, aid)
        state, _ = check_in(conn, "test-secret", code)
        assert state != STATE_VALID
