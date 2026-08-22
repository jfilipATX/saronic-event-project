"""Feature #6 — QR check-in, Option A (invite-list anchored, single event QR).

Token design (code-review gate #3):
  * Each invitee gets a signed credential minted in advance:
        checkin_code = f"{invite_id}.{issued_at}.{hmac_hex}"
    where hmac_hex = HMAC-SHA256(secret, f"{invite_id}.{issued_at}"). No PII in the
    payload; the attendee's name/email stay server-side in the DB.
  * The *event* QR is just a deep-link to /checkin (the entry point, not the
    credential). The attendee then presents their personal signed code.
  * Verification returns one of three states, mapping 1:1 to DESIGN.md scan tokens:
        VALID      -> good token + first scan  (mark attended)
        ALREADY    -> token already used        (replay rejected)
        TAMPERED   -> bad HMAC or unknown id    (rejected)
  * Walk-ins (no token) self-add a record flagged self_reported=True.

This module is pure-stdlib and side-effect free except via the repository, so it is
fully unit-testable offline.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from app.db.models import Attendee
from app.db import repository as repo
import sqlite3

STATE_VALID = "valid"
STATE_ALREADY = "already"
STATE_TAMPERED = "tampered"
STATE_UNKNOWN = "unknown"
#: A real invitation that has since been cancelled. Deliberately distinct from
#: TAMPERED so the desk treats it as admin, not as a forgery.
STATE_WITHDRAWN = "withdrawn"


def _hmac(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def mint_code(secret: str, invite_id: int, issued_at: int | None = None) -> str:
    """Create a signed check-in credential for an invitee."""
    issued_at = issued_at or int(time.time())
    payload = f"{invite_id}.{issued_at}"
    return f"{payload}.{_hmac(secret, payload)}"


def _split(code: str):
    parts = code.split(".")
    if len(parts) != 3:
        return None
    invite_id_s, issued_at_s, sig = parts
    try:
        return int(invite_id_s), int(issued_at_s), sig
    except ValueError:
        return None


def verify_code(secret: str, code: str) -> tuple[str, Attendee | None]:
    """Return (state, attendee) for a presented check-in code."""
    parsed = _split(code)
    if parsed is None:
        return STATE_TAMPERED, None
    invite_id, issued_at, sig = parsed
    expected = _hmac(secret, f"{invite_id}.{issued_at}")
    # Constant-time compare to avoid timing side-channels.
    if not hmac.compare_digest(expected, sig):
        return STATE_TAMPERED, None
    return STATE_VALID, None  # attendee resolution happens against the DB in check_in()


def check_in(conn: sqlite3.Connection, secret: str, code: str,
             when: str | None = None) -> tuple[str, Attendee | None]:
    """Full check-in flow against the DB. Returns (state, attendee)."""
    when = when or _now()
    state, _ = verify_code(secret, code)
    if state != STATE_VALID:
        return state, None
    parsed = _split(code)
    assert parsed is not None
    invite_id, _, _ = parsed
    attendee = repo.get_attendee_by_code(conn, code)
    if attendee is None:
        return STATE_TAMPERED, None
    # A cancelled invitation must not open the door. Reported as WITHDRAWN, not
    # TAMPERED: a cancelled guest at the desk is an admin problem the staff
    # member can resolve, and calling it tampering turns it into a security
    # incident and an awkward conversation.
    if attendee.is_withdrawn:
        return STATE_WITHDRAWN, attendee
    if attendee.attended_at is not None:
        return STATE_ALREADY, attendee
    repo.mark_attended(conn, attendee.id, when)
    attendee.attended_at = when
    return STATE_VALID, attendee


def self_check_in(conn: sqlite3.Connection, event_id: int, full_name: str,
                  email: str | None = None,
                  when: str | None = None) -> Attendee:
    """Walk-in with no token: create a self-reported attendee record and mark."""
    when = when or _now()
    att = Attendee(event_id=event_id, full_name=full_name, email=email,
                   self_reported=True)
    aid = repo.add_attendee(conn, att)
    repo.mark_attended(conn, aid, when)
    att.id = aid
    att.attended_at = when
    return att


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
