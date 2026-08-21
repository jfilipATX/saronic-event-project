"""Feature #6 — QR check-in. Security-sensitive: signed tokens + attendee PII.

This module mints credentials and admits people to an event, so the tests are
adversarial by design: forged signatures, replayed tokens, cross-event reuse,
malformed input, and PII leakage into the token payload.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Attendee, Event
from app.features.qr_checkin import (
    STATE_ALREADY,
    STATE_TAMPERED,
    STATE_VALID,
    check_in,
    mint_code,
    self_check_in,
    verify_code,
)

SECRET = "test-signing-secret-not-a-real-one"
OTHER_SECRET = "a-different-secret"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def event_id(conn) -> int:
    return repo.create_event(conn, Event(name="Saronic Fleet Week", city="Austin"))


@pytest.fixture()
def invitee(conn, event_id):
    code = mint_code(SECRET, invite_id=1, issued_at=1_700_000_000)
    aid = repo.add_attendee(conn, Attendee(
        event_id=event_id, full_name="Dana Reyes",
        email="dana@example.com", checkin_code=code))
    return aid, code


class TestMintCode:
    def test_code_has_three_dotted_parts(self):
        assert len(mint_code(SECRET, 1, 1_700_000_000).split(".")) == 3

    def test_code_is_deterministic_for_the_same_inputs(self):
        a = mint_code(SECRET, 7, 1_700_000_000)
        b = mint_code(SECRET, 7, 1_700_000_000)
        assert a == b

    def test_different_invitees_get_different_codes(self):
        assert mint_code(SECRET, 1, 1_700_000_000) != mint_code(SECRET, 2, 1_700_000_000)

    def test_different_secrets_produce_different_signatures(self):
        a = mint_code(SECRET, 1, 1_700_000_000)
        b = mint_code(OTHER_SECRET, 1, 1_700_000_000)
        assert a != b

    def test_code_carries_no_pii(self):
        """Names/emails must never ride in a token that gets printed on a badge."""
        code = mint_code(SECRET, 1, 1_700_000_000)
        lowered = code.lower()
        for leak in ("dana", "reyes", "@", "example.com"):
            assert leak not in lowered


class TestVerifyCode:
    def test_freshly_minted_code_verifies(self):
        state, _ = verify_code(SECRET, mint_code(SECRET, 1, 1_700_000_000))
        assert state == STATE_VALID

    def test_forged_signature_is_rejected(self):
        code = mint_code(SECRET, 1, 1_700_000_000)
        body, _sig = code.rsplit(".", 1)
        assert verify_code(SECRET, f"{body}.{'0' * 64}")[0] == STATE_TAMPERED

    def test_code_signed_with_another_secret_is_rejected(self):
        foreign = mint_code(OTHER_SECRET, 1, 1_700_000_000)
        assert verify_code(SECRET, foreign)[0] == STATE_TAMPERED

    def test_tampered_invite_id_is_rejected(self):
        """Escalating to another invitee's id must not survive verification."""
        _id, issued, sig = mint_code(SECRET, 1, 1_700_000_000).split(".")
        assert verify_code(SECRET, f"2.{issued}.{sig}")[0] == STATE_TAMPERED

    def test_tampered_timestamp_is_rejected(self):
        invite, _issued, sig = mint_code(SECRET, 1, 1_700_000_000).split(".")
        assert verify_code(SECRET, f"{invite}.1700009999.{sig}")[0] == STATE_TAMPERED

    @pytest.mark.parametrize("junk", [
        "", ".", "..", "not-a-code", "1.2", "1.2.3.4",
        "abc.def.ghi", "1.notanint.aaaa", "  .  .  ",
    ])
    def test_malformed_input_is_rejected_without_raising(self, junk):
        assert verify_code(SECRET, junk)[0] == STATE_TAMPERED


class TestCheckIn:
    def test_valid_first_scan_marks_attendance(self, conn, invitee):
        _aid, code = invitee
        state, attendee = check_in(conn, SECRET, code, when="2026-08-26 09:00:00")
        assert state == STATE_VALID
        assert attendee.full_name == "Dana Reyes"
        assert attendee.attended_at == "2026-08-26 09:00:00"

    def test_attendance_is_persisted_not_just_returned(self, conn, invitee):
        _aid, code = invitee
        check_in(conn, SECRET, code, when="2026-08-26 09:00:00")
        stored = repo.get_attendee_by_code(conn, code)
        assert stored.attended_at == "2026-08-26 09:00:00"

    def test_replayed_token_is_rejected_as_already_used(self, conn, invitee):
        _aid, code = invitee
        check_in(conn, SECRET, code, when="2026-08-26 09:00:00")
        state, attendee = check_in(conn, SECRET, code, when="2026-08-26 09:05:00")
        assert state == STATE_ALREADY
        # The original timestamp must not be overwritten by the replay.
        assert attendee.attended_at == "2026-08-26 09:00:00"

    def test_wellformed_but_unissued_code_is_rejected(self, conn, event_id):
        """Correctly signed but never issued to anyone — must not admit."""
        orphan = mint_code(SECRET, invite_id=999, issued_at=1_700_000_000)
        assert check_in(conn, SECRET, orphan)[0] == STATE_TAMPERED

    def test_forged_code_never_reaches_the_database(self, conn, invitee):
        _aid, code = invitee
        body, _sig = code.rsplit(".", 1)
        state, attendee = check_in(conn, SECRET, f"{body}.{'f' * 64}")
        assert state == STATE_TAMPERED
        assert attendee is None
        assert repo.get_attendee_by_code(conn, code).attended_at is None

    def test_two_invitees_check_in_independently(self, conn, event_id):
        codes = []
        for i in (1, 2):
            c = mint_code(SECRET, invite_id=i, issued_at=1_700_000_000)
            repo.add_attendee(conn, Attendee(
                event_id=event_id, full_name=f"Guest {i}", checkin_code=c))
            codes.append(c)
        assert check_in(conn, SECRET, codes[0])[0] == STATE_VALID
        assert check_in(conn, SECRET, codes[1])[0] == STATE_VALID


class TestSelfCheckIn:
    def test_walk_in_is_recorded_and_flagged(self, conn, event_id):
        att = self_check_in(conn, event_id, "Walk In", when="2026-08-26 09:30:00")
        assert att.self_reported is True
        assert att.attended_at == "2026-08-26 09:30:00"

    def test_walk_in_is_persisted_against_the_event(self, conn, event_id):
        self_check_in(conn, event_id, "Walk In", when="2026-08-26 09:30:00")
        listed = repo.list_attendees(conn, event_id)
        assert [a.full_name for a in listed] == ["Walk In"]
        assert listed[0].self_reported is True

    def test_walk_in_has_no_checkin_code(self, conn, event_id):
        """A self-reported record must not masquerade as a signed credential."""
        att = self_check_in(conn, event_id, "Walk In")
        stored = repo.list_attendees(conn, event_id)[0]
        assert att.checkin_code is None
        assert stored.checkin_code is None

    def test_walk_ins_are_distinguishable_from_verified_attendees(self, conn, invitee, event_id):
        _aid, code = invitee
        check_in(conn, SECRET, code)
        self_check_in(conn, event_id, "Walk In")
        listed = repo.list_attendees(conn, event_id)
        verified = [a for a in listed if not a.self_reported]
        walkins = [a for a in listed if a.self_reported]
        assert len(verified) == 1 and len(walkins) == 1
