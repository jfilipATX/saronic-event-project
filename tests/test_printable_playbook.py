"""P5-7 — Printable playbook (offline day-of operational reference).

A separate, print-optimized view at /events/{id}/playbook/print: chrome hidden,
white backgrounds, amber double-booking kept via print-color-adjust: exact,
attendee contact PII excluded, day-of binder density. Reads the SAME
compose_playbook + run-of-show + ledger as the screen view, so print and screen
never disagree.

Acceptance checks (mirror Edye's spec):
1. Print view renders without nav/buttons/stepper chrome.
2. Run-of-show groups by day with owners.
3. Double-booked owner flagged (amber text) AND the flag carries
   `print-color-adjust: exact` so it survives print.
4. Location shows state + country when set.
5. PII discipline: attendee contact (email) absent from the print view; VIPs
   show only name/company/tier.
6. Missing venue/owner -> "Not set", not a 500.
"""
from __future__ import annotations

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event
from app.main import create_app


def _conn():
    # Autocommit (isolation_level=None) so writes are immediately visible to the
    # app's separate connection when the test seeds people/segments/attendees.
    c = sqlite3.connect(os.environ["DB_PATH"], isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture()
def client():
    import tempfile
    d = tempfile.mkdtemp()
    os.environ["DB_PATH"] = os.path.join(d, "t.db")
    try:
        yield TestClient(create_app())
    finally:
        os.environ.pop("DB_PATH", None)


@pytest.fixture()
def event_id(client):
    r = client.post("/events", data={"name": "Fleet Week", "city": "Austin",
                                      "state": "TX", "country": "US",
                                      "owner_name": "Lt. Cmdr. Reyes",
                                      "owner_role": "Event Lead"},
                    follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


class TestPrintChrome:
    def test_no_nav_or_buttons_in_print_view(self, client, event_id):
        html = client.get(f"/events/{event_id}/playbook/print").text
        # The print template is a standalone document, not the screen shell:
        # no navigation links, no interactive buttons, no stepper chrome.
        assert "nav-link" not in html
        assert "<button" not in html
        assert "class=\"stepper\"" not in html
        assert "Playbook" in html  # content present


class TestContent:
    def test_location_shows_state_and_country(self, client, event_id):
        html = client.get(f"/events/{event_id}/playbook/print").text
        assert "Austin" in html and "TX" in html and "US" in html

    def test_missing_owner_renders_not_set(self, client):
        r = client.post("/events", data={"name": "Bare", "city": "Boston",
                                         "country": "US"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        html = client.get(f"/events/{eid}/playbook/print").text
        assert "Bare" in html
        assert "Not set" in html

    def test_ros_groups_by_day_with_owners(self, client, event_id):
        pid = repo.add_person(_conn(), repo.Person(name="Dana",
                                                                 role="Lead"))
        repo.assign_staff(_conn(), event_id, pid, role="Lead",
                          can_check_in=True)
        seg = repo.Segment(event_id=event_id, title="Expo booth",
                           start="2026-03-14 10:00", end="2026-03-14 12:00",
                           track="Expo", owner_ids=[pid])
        repo.add_segment(_conn(), seg)
        html = client.get(f"/events/{event_id}/playbook/print").text
        assert "Expo booth" in html
        assert "Dana" in html


class TestDoubleBookAmber:
    def test_amber_flag_survives_print(self, client, event_id):
        pid = repo.add_person(_conn(), repo.Person(name="Sam"))
        repo.assign_staff(_conn(), event_id, pid, role="Ops")
        s1 = repo.Segment(event_id=event_id, title="Briefing",
                          start="2026-03-14 10:00", end="2026-03-14 11:00",
                          track="Ops", owner_ids=[pid])
        s2 = repo.Segment(event_id=event_id, title="Walkthrough",
                          start="2026-03-14 10:30", end="2026-03-14 11:30",
                          track="Ops", owner_ids=[pid])
        repo.add_segment(_conn(), s1)
        repo.add_segment(_conn(), s2)
        html = client.get(f"/events/{event_id}/playbook/print").text
        assert "double-booked" in html.lower() or "double booked" in html.lower()
        # The amber flag element exists AND carries the print-color-adjust rule
        # so it survives onto paper (spec requirement).
        assert "print-ros-conflict" in html
        assert "print-color-adjust" in html and "exact" in html


class TestPiiDiscipline:
    def test_attendee_email_absent_vip_shows_name_company_only(self, client,
                                                                event_id):
        # A VIP with an email + a non-VIP with an email.
        conn = _conn()
        repo.add_attendee(conn, repo.Attendee(event_id=event_id,
                                              full_name="Jane Smith",
                                              email="jane@x.com",
                                              company="State Dept",
                                              is_vip=True))
        repo.add_attendee(conn, repo.Attendee(event_id=event_id,
                                              full_name="Bob Roe",
                                              email="bob@y.com",
                                              company="Contractor",
                                              is_vip=False))
        html = client.get(f"/events/{event_id}/playbook/print").text
        # No raw email addresses leak onto a shared paper doc.
        assert "jane@x.com" not in html
        assert "bob@y.com" not in html
        # VIP name + company present (what a desk operator needs).
        assert "Jane Smith" in html
        assert "State Dept" in html


class TestSpend:
    def test_spend_line_shows_offline(self, client, event_id):
        html = client.get(f"/events/{event_id}/playbook/print").text
        assert "none" in html.lower() and "offline" in html.lower()


class TestPrintReadabilityP63:
    """P6-3 — the day-of playbook must read cleanly on paper, not just render.

    These pin the readability pass: drop the near-always-empty Location column
    (wasted horizontal space), add a generated footer, and keep PII out.
    """

    def test_ros_table_drops_empty_location_column(self, client, event_id):
        conn = _conn()
        pid = repo.add_person(conn, repo.Person(name="Sam"))
        repo.assign_staff(conn, event_id, pid, role="Ops")
        repo.add_segment(conn, repo.Segment(event_id=event_id, title="Briefing",
            start="2026-03-14 10:00", end="2026-03-14 11:00", track="Ops",
            owner_ids=[pid]))
        html = client.get(f"/events/{event_id}/playbook/print").text
        # The ROS header row no longer carries a Location column.
        assert "<th>Location</th>" not in html
        # Sections + segments still present and readable.
        assert "Run of show" in html
        assert "Briefing" in html

    def test_footer_marked_day_of_reference(self, client, event_id):
        html = client.get(f"/events/{event_id}/playbook/print").text
        assert "day-of reference only" in html

    def test_emails_still_excluded_after_readability_pass(self, client, event_id):
        conn = _conn()
        repo.add_attendee(conn, repo.Attendee(event_id=event_id,
            full_name="Jane Smith", email="jane@x.com", company="State Dept",
            is_vip=True))
        html = client.get(f"/events/{event_id}/playbook/print").text
        assert "jane@x.com" not in html
        assert "Jane Smith" in html
