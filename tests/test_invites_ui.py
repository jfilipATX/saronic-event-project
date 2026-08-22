"""Invite issuance from the browser — the phase-1 gap.

``issue_invite()`` existed, was tested, and minted valid credentials, but was
reachable only from a Python call. A coordinator could scan a QR credential
they had no way to create.

Two properties beyond "it works":

* **Issuing to someone already on the roster mints their code**, rather than
  creating a second person. The roster comes from CSV import; issuing invites
  afterwards is the normal order, not an edge case.
* **Validation failures keep what was typed.** Re-rendering an empty form makes
  the coordinator retype everything, which is how the walk-in form annoyed the
  desk.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.db import repository as repo
from app.main import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(db_path=str(tmp_path / "t.db")))


@pytest.fixture()
def eid(client) -> int:
    r = client.post("/events", data={"name": "Fleet Week", "city": "Austin"},
                    follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


def _conn():
    import sqlite3

    conn = sqlite3.connect(main_mod.CURRENT_DB)
    conn.row_factory = sqlite3.Row
    return conn


class TestInviteScreen:
    def test_the_roster_page_offers_invite_issuance(self, client, eid):
        assert "/invites" in client.get(f"/events/{eid}/roster").text

    def test_the_invite_page_renders(self, client, eid):
        page = client.get(f"/events/{eid}/invites")
        assert page.status_code == 200
        assert 'name="full_name"' in page.text
        assert 'name="email"' in page.text


class TestIssuingAnInvite:
    def test_issuing_creates_an_invitee_with_a_code(self, client, eid):
        client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "dana@example.com"})
        conn = _conn()
        try:
            people = repo.list_attendees(conn, eid)
        finally:
            conn.close()
        assert len(people) == 1
        assert people[0].checkin_code

    def test_the_code_is_shown_to_the_coordinator(self, client, eid):
        page = client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "dana@example.com"})
        conn = _conn()
        try:
            code = repo.list_attendees(conn, eid)[0].checkin_code
        finally:
            conn.close()
        assert code in page.text

    def test_the_issued_credential_actually_scans(self, client, eid):
        """The point of the feature: a code you can create and then use."""
        client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "dana@example.com"})
        conn = _conn()
        try:
            code = repo.list_attendees(conn, eid)[0].checkin_code
        finally:
            conn.close()
        page = client.post(f"/events/{eid}/checkin", data={"code": code})
        assert "scan-valid" in page.text

    def test_an_invitee_is_not_self_reported(self, client, eid):
        client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "dana@example.com"})
        conn = _conn()
        try:
            assert repo.list_attendees(conn, eid)[0].self_reported is False
        finally:
            conn.close()

    def test_optional_details_are_stored(self, client, eid):
        client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "dana@example.com",
            "title": "Program Director", "company": "Saronic", "is_vip": "1"})
        conn = _conn()
        try:
            person = repo.list_attendees(conn, eid)[0]
        finally:
            conn.close()
        assert (person.title, person.company) == ("Program Director", "Saronic")
        assert person.is_vip is True

    def test_issuing_to_someone_already_on_the_roster_mints_their_code(
            self, client, eid):
        """Import a roster, then invite them — one person, now with a code."""
        csv_text = "name,email\nDana Reyes,dana@example.com\n"
        from app.features.roster_import import preview_csv

        data = {"csv_text": csv_text}
        data.update({f"map_{h}": f for h, f in preview_csv(csv_text).mapping.items()})
        client.post(f"/events/{eid}/roster/import", data=data)
        client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "dana@example.com"})
        conn = _conn()
        try:
            people = repo.list_attendees(conn, eid)
        finally:
            conn.close()
        assert len(people) == 1
        assert people[0].checkin_code

    def test_re_issuing_does_not_duplicate_the_person(self, client, eid):
        for _ in range(3):
            client.post(f"/events/{eid}/invites", data={
                "full_name": "Dana Reyes", "email": "dana@example.com"})
        conn = _conn()
        try:
            assert len(repo.list_attendees(conn, eid)) == 1
        finally:
            conn.close()

    def test_a_withdrawn_person_can_be_re_invited(self, client, eid):
        client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "dana@example.com"})
        conn = _conn()
        try:
            aid = repo.list_attendees(conn, eid)[0].id
            repo.withdraw_attendee(conn, aid)
            conn.commit()
        finally:
            conn.close()
        client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "dana@example.com"})
        conn = _conn()
        try:
            assert len(repo.list_attendees(conn, eid)) == 1
        finally:
            conn.close()


class TestValidationKeepsWhatWasTyped:
    def test_a_missing_email_is_refused_with_a_reason(self, client, eid):
        page = client.post(f"/events/{eid}/invites", data={"full_name": "Dana Reyes"})
        assert page.status_code == 200
        assert "email" in page.text.lower()

    def test_the_typed_name_survives_a_validation_failure(self, client, eid):
        """Designer's note: re-rendering empty makes the desk retype everything."""
        page = client.post(f"/events/{eid}/invites",
                           data={"full_name": "Dana Reyes", "company": "Saronic"})
        assert 'value="Dana Reyes"' in page.text
        assert 'value="Saronic"' in page.text

    def test_a_malformed_email_is_refused(self, client, eid):
        page = client.post(f"/events/{eid}/invites", data={
            "full_name": "Dana Reyes", "email": "not-an-email"})
        assert "not a valid email" in page.text.lower()

    def test_nothing_is_created_on_a_validation_failure(self, client, eid):
        client.post(f"/events/{eid}/invites", data={"full_name": "Dana Reyes"})
        conn = _conn()
        try:
            assert repo.list_attendees(conn, eid) == []
        finally:
            conn.close()

    def test_all_missing_fields_are_named_at_once(self, client, eid):
        page = client.post(f"/events/{eid}/invites", data={})
        text = page.text.lower()
        assert "name" in text and "email" in text
