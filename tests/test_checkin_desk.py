"""P2-5 — check-in by email, richer walk-ins, and VIP alerting.

Three related pieces:

* **Email check-in** — for testing, and for the real case where someone arrives
  without their QR code. An invited person is checked in by looking them up;
  it is not a walk-in, and the record must not claim it was.
* **Walk-ins require name, email, title and company** — a walk-in at a defense
  event is someone whose details we did not have, so the desk collects them.
* **VIP alerting** — the coordinator is told when a VIP arrives, without the
  flag ever affecting whether someone is admitted.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Attendee, Event
from app.features.qr_checkin import (
    STATE_ALREADY,
    STATE_UNKNOWN,
    STATE_VALID,
    STATE_WITHDRAWN,
    check_in_by_email,
    register_walk_in,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def event(conn) -> int:
    return repo.create_event(conn, Event(name="Fleet Week", city="Austin"))


def _invite(conn, event_id, name="Dana Reyes", email="dana@example.com", vip=False):
    return repo.add_attendee(conn, Attendee(
        event_id=event_id, full_name=name, email=email, is_vip=vip))


class TestCheckInByEmail:
    def test_an_invited_person_checks_in(self, conn, event):
        _invite(conn, event)
        state, person = check_in_by_email(conn, event, "dana@example.com")
        assert state == STATE_VALID
        assert person.full_name == "Dana Reyes"

    def test_attendance_is_recorded(self, conn, event):
        aid = _invite(conn, event)
        check_in_by_email(conn, event, "dana@example.com")
        assert repo.get_attendee(conn, aid).attended_at is not None

    def test_email_matching_ignores_case_and_whitespace(self, conn, event):
        _invite(conn, event)
        state, _ = check_in_by_email(conn, event, "  DANA@Example.COM ")
        assert state == STATE_VALID

    def test_an_unknown_email_is_reported_not_created(self, conn, event):
        state, person = check_in_by_email(conn, event, "stranger@example.com")
        assert state == STATE_UNKNOWN
        assert person is None
        assert repo.list_attendees(conn, event) == []

    def test_checking_in_twice_reports_already(self, conn, event):
        _invite(conn, event)
        check_in_by_email(conn, event, "dana@example.com")
        state, _ = check_in_by_email(conn, event, "dana@example.com")
        assert state == STATE_ALREADY

    def test_a_withdrawn_invitee_cannot_check_in_by_email(self, conn, event):
        """The same gate as the QR path — removal must reach every door."""
        aid = _invite(conn, event)
        repo.withdraw_attendee(conn, aid)
        state, _ = check_in_by_email(conn, event, "dana@example.com")
        assert state == STATE_WITHDRAWN

    def test_an_erased_person_cannot_be_found_by_email(self, conn, event):
        aid = _invite(conn, event)
        repo.erase_attendee(conn, aid)
        state, _ = check_in_by_email(conn, event, "dana@example.com")
        assert state == STATE_UNKNOWN

    def test_email_checkin_is_not_marked_self_reported(self, conn, event):
        """They were on the invite list; the record must not imply otherwise."""
        aid = _invite(conn, event)
        check_in_by_email(conn, event, "dana@example.com")
        assert repo.get_attendee(conn, aid).self_reported is False

    def test_a_person_invited_to_another_event_is_not_found(self, conn, event):
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        _invite(conn, other)
        state, _ = check_in_by_email(conn, event, "dana@example.com")
        assert state == STATE_UNKNOWN

    def test_a_blank_email_is_rejected(self, conn, event):
        with pytest.raises(ValueError):
            check_in_by_email(conn, event, "   ")


class TestWalkIns:
    def test_all_four_fields_are_recorded(self, conn, event):
        person = register_walk_in(conn, event, full_name="Ada Fournier",
                                  email="ada@example.com", title="Fleet Ops",
                                  company="Navantia")
        assert (person.full_name, person.email) == ("Ada Fournier", "ada@example.com")
        assert (person.title, person.company) == ("Fleet Ops", "Navantia")

    def test_a_walk_in_is_marked_self_reported(self, conn, event):
        """Their details are unverified — the badge count must not pretend
        otherwise."""
        person = register_walk_in(conn, event, full_name="Ada", email="a@x.com",
                                  title="Ops", company="Navantia")
        assert person.self_reported is True

    def test_a_walk_in_is_immediately_attended(self, conn, event):
        person = register_walk_in(conn, event, full_name="Ada", email="a@x.com",
                                  title="Ops", company="Navantia")
        assert person.attended_at is not None

    @pytest.mark.parametrize("missing", ["full_name", "email", "title", "company"])
    def test_every_field_is_required(self, conn, event, missing):
        fields = {"full_name": "Ada", "email": "a@x.com", "title": "Ops",
                  "company": "Navantia"}
        fields[missing] = "  "
        with pytest.raises(ValueError, match=missing.replace("_", " ")):
            register_walk_in(conn, event, **fields)

    def test_a_malformed_email_is_rejected(self, conn, event):
        with pytest.raises(ValueError, match="email"):
            register_walk_in(conn, event, full_name="Ada", email="not-an-email",
                             title="Ops", company="Navantia")

    def test_an_already_invited_person_is_checked_in_not_duplicated(self, conn, event):
        """Someone on the list who says they are a walk-in is still one person."""
        _invite(conn, event, "Dana Reyes", "dana@example.com")
        register_walk_in(conn, event, full_name="Dana Reyes",
                         email="dana@example.com", title="Director",
                         company="Saronic")
        assert len(repo.list_attendees(conn, event)) == 1

    def test_that_person_is_not_downgraded_to_self_reported(self, conn, event):
        aid = _invite(conn, event, "Dana Reyes", "dana@example.com")
        register_walk_in(conn, event, full_name="Dana Reyes",
                         email="dana@example.com", title="Director",
                         company="Saronic")
        assert repo.get_attendee(conn, aid).self_reported is False


class TestVipAlerting:
    def test_a_vip_arrival_is_flagged(self, conn, event):
        _invite(conn, event, vip=True)
        _, person = check_in_by_email(conn, event, "dana@example.com")
        assert person.is_vip is True

    def test_a_vip_arrival_is_recorded_for_the_coordinator(self, conn, event):
        _invite(conn, event, vip=True)
        check_in_by_email(conn, event, "dana@example.com")
        alerts = repo.vip_alerts(conn, event)
        assert len(alerts) == 1
        assert alerts[0].attendee_name == "Dana Reyes"

    def test_a_non_vip_arrival_raises_no_alert(self, conn, event):
        _invite(conn, event, vip=False)
        check_in_by_email(conn, event, "dana@example.com")
        assert repo.vip_alerts(conn, event) == []

    def test_the_alert_records_that_email_was_not_actually_sent(self, conn, event):
        """Honest by default: we log what WOULD be sent until SMTP is configured,
        rather than implying a notification went out."""
        _invite(conn, event, vip=True)
        check_in_by_email(conn, event, "dana@example.com")
        assert repo.vip_alerts(conn, event)[0].delivered is False

    def test_a_second_scan_does_not_re_alert(self, conn, event):
        _invite(conn, event, vip=True)
        check_in_by_email(conn, event, "dana@example.com")
        check_in_by_email(conn, event, "dana@example.com")
        assert len(repo.vip_alerts(conn, event)) == 1

    def test_vip_status_never_affects_admission(self, conn, event):
        """The flag is for attention, not access."""
        aid = _invite(conn, event, vip=True)
        repo.withdraw_attendee(conn, aid)
        state, _ = check_in_by_email(conn, event, "dana@example.com")
        assert state == STATE_WITHDRAWN
        assert repo.vip_alerts(conn, event) == []

    def test_a_vip_walk_in_also_alerts(self, conn, event):
        person = register_walk_in(conn, event, full_name="Ada", email="a@x.com",
                                  title="Ops", company="Navantia", is_vip=True)
        assert person.is_vip is True
        assert len(repo.vip_alerts(conn, event)) == 1
