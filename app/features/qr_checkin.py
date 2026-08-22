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

import re

import hashlib
import hmac
import time

from app.db.models import Attendee, VipAlert
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


#: Deliberately permissive, matching the roster importer: a visible typo beats a
#: silently rejected guest standing at the desk.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _raise_vip_alert(conn: sqlite3.Connection, attendee: Attendee, when: str) -> None:
    """Record a VIP arrival. Never gates admission — attention, not access."""
    if not attendee.is_vip:
        return
    repo.record_vip_alert(conn, VipAlert(
        event_id=attendee.event_id,
        attendee_id=attendee.id,
        attendee_name=attendee.full_name or "",
        company=attendee.company,
        arrived_at=when,
        delivered=False,
    ))


def check_in_by_email(conn: sqlite3.Connection, event_id: int, email: str,
                      when: str | None = None) -> tuple[str, Attendee | None]:
    """Check in an invited person by email, for arrivals without their QR code.

    This is a lookup, not a registration: an unknown email is reported so the
    desk can decide, rather than quietly creating an attendee. Someone who was
    invited must never be recorded as self-reported.
    """
    if not email or not email.strip():
        raise ValueError("Enter an email address to look up.")
    when = when or _now()
    attendee = repo.get_attendee_by_email(conn, event_id, email)
    if attendee is None:
        return STATE_UNKNOWN, None
    if attendee.is_withdrawn:
        return STATE_WITHDRAWN, attendee
    if attendee.attended_at is not None:
        return STATE_ALREADY, attendee
    repo.mark_attended(conn, attendee.id, when)
    attendee.attended_at = when
    _raise_vip_alert(conn, attendee, when)
    return STATE_VALID, attendee


def register_walk_in(conn: sqlite3.Connection, event_id: int, full_name: str,
                     email: str, title: str, company: str,
                     is_vip: bool = False,
                     when: str | None = None) -> Attendee:
    """Register someone who arrived without an invitation.

    All four fields are required: a walk-in at a defense event is precisely the
    person whose details we did not already have, so the desk collects them.

    If the email is already on the roster this checks that person in instead of
    creating a second record — someone on the list who believes they are a
    walk-in is still one person, and must not be downgraded to self-reported.
    """
    fields = {"full name": full_name, "email": email, "title": title,
              "company": company}
    missing = [label for label, value in fields.items()
               if not value or not value.strip()]
    if missing:
        # Name everything missing at once ("an email" not "a email", and no
        # resubmit-to-discover-the-next-gap loop for the desk operator).
        listed = ", ".join(missing[:-1]) + (" and " if len(missing) > 1 else "") + missing[-1]
        raise ValueError(f"A walk-in needs {listed}. All four fields are required.")
    email = email.strip()
    if not _EMAIL_RE.match(email):
        raise ValueError(f"{email!r} is not a valid email address.")

    when = when or _now()
    existing = repo.get_attendee_by_email(conn, event_id, email)
    if existing is not None and not existing.is_withdrawn:
        if existing.attended_at is None:
            repo.mark_attended(conn, existing.id, when)
            existing.attended_at = when
            _raise_vip_alert(conn, existing, when)
        return existing

    attendee = Attendee(
        event_id=event_id, full_name=full_name.strip(), email=email,
        title=title.strip(), company=company.strip(), is_vip=is_vip,
        self_reported=True,
    )
    aid = repo.add_attendee(conn, attendee)
    repo.mark_attended(conn, aid, when)
    attendee.id = aid
    attendee.attended_at = when
    _raise_vip_alert(conn, attendee, when)
    return attendee


def issue_invitation(conn: sqlite3.Connection, secret: str, event_id: int,
                     full_name: str, email: str, title: str = "",
                     company: str = "", is_vip: bool = False) -> Attendee:
    """Mint a signed credential for an invitee, from the browser.

    Idempotent on email: issuing to someone already on the roster mints THEIR
    code rather than creating a second person. Importing a roster and then
    issuing invites is the normal order of work, not an edge case, and a
    duplicate would mean two credentials for one guest.

    Re-issuing to a withdrawn person reinstates them: the coordinator asking for
    an invitation is a clearer signal of intent than the earlier cancellation.
    """
    missing = [label for label, value in
               (("a name", full_name), ("an email address", email))
               if not value or not value.strip()]
    if missing:
        raise ValueError("An invitation needs " + " and ".join(missing) + ".")
    email = email.strip()
    if not _EMAIL_RE.match(email):
        raise ValueError(f"{email!r} is not a valid email address.")

    existing = repo.get_attendee_by_email(conn, event_id, email)
    if existing is not None:
        if existing.is_withdrawn:
            repo.reinstate_attendee(conn, existing.id)
        code = existing.checkin_code or mint_code(secret, existing.id)
        conn.execute("UPDATE attendees SET checkin_code=?, full_name=?, "
                     "title=COALESCE(NULLIF(?,''), title), "
                     "company=COALESCE(NULLIF(?,''), company), "
                     "is_vip=? WHERE id=?",
                     (code, full_name.strip(), title.strip(), company.strip(),
                      int(bool(is_vip)), existing.id))
        return repo.get_attendee(conn, existing.id)

    attendee = Attendee(
        event_id=event_id, full_name=full_name.strip(), email=email,
        title=title.strip() or None, company=company.strip() or None,
        is_vip=is_vip, self_reported=False,
    )
    aid = repo.add_attendee(conn, attendee)
    code = mint_code(secret, invite_id=aid)
    conn.execute("UPDATE attendees SET checkin_code=? WHERE id=?", (code, aid))
    return repo.get_attendee(conn, aid)
