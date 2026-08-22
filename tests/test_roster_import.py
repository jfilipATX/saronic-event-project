"""P2-5 — CSV roster import with a column-mapping preview.

Spreadsheets from other sources are never clean, so the design rule is that bad
rows are *reported*, never fatal: an all-or-nothing import punishes the
coordinator for someone else's data entry.

Two properties matter beyond parsing:

* **Nothing is written until the coordinator commits.** Upload previews and
  proposes a mapping; the import is a separate act.
* **A duplicate email is not a new person.** Roster files get re-sent with
  additions, and re-importing must not double the room.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Attendee, Event
from app.features.roster_import import (
    REQUIRED_FIELDS,
    ImportOutcome,
    RosterPreview,
    apply_roster,
    guess_mapping,
    preview_csv,
)

CLEAN = (
    "Full Name,Email Address,Job Title,Company,VIP\n"
    "Dana Reyes,dana@example.com,Program Director,Saronic,yes\n"
    "Sam Okoye,sam@example.com,Naval Architect,Damen,no\n"
    "Ada Fournier,ada@example.com,Fleet Ops Lead,Navantia,\n"
)

MESSY = (
    "name,email,title,company\n"
    "Dana Reyes,dana@example.com,Director,Saronic\n"
    ",no-name@example.com,Analyst,Acme\n"          # missing name
    "Blank Email,,Engineer,Acme\n"                  # missing email
    "Bad Email,not-an-email,Engineer,Acme\n"        # malformed
    "Sam Okoye,sam@example.com,Architect,Damen\n"
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


class TestGuessMapping:
    def test_recognises_common_header_names(self):
        m = guess_mapping(["Full Name", "Email Address", "Job Title", "Company", "VIP"])
        assert m["Full Name"] == "full_name"
        assert m["Email Address"] == "email"
        assert m["Job Title"] == "title"
        assert m["Company"] == "company"
        assert m["VIP"] == "vip"

    def test_is_case_and_punctuation_insensitive(self):
        m = guess_mapping(["FULL_NAME", "e-mail", "  Title  "])
        assert m["FULL_NAME"] == "full_name"
        assert m["e-mail"] == "email"
        assert m["  Title  "] == "title"

    def test_unrecognised_headers_map_to_ignore(self):
        assert guess_mapping(["Dietary Requirements"])["Dietary Requirements"] == "ignore"

    def test_does_not_map_two_headers_to_the_same_field(self):
        """'Name' and 'Full Name' both look like a name; only one may win."""
        m = guess_mapping(["Name", "Full Name"])
        assert list(m.values()).count("full_name") == 1


class TestPreview:
    def test_returns_headers_and_the_first_rows_only(self):
        preview = preview_csv(CLEAN, sample_size=2)
        assert preview.headers[0] == "Full Name"
        assert len(preview.sample_rows) == 2

    def test_counts_every_data_row_not_just_the_sample(self):
        assert preview_csv(CLEAN, sample_size=1).total_rows == 3

    def test_proposes_a_mapping(self):
        assert preview_csv(CLEAN).mapping["Email Address"] == "email"

    def test_reports_which_required_fields_are_unmapped(self):
        preview = preview_csv("Company,Title\nAcme,Engineer\n")
        assert set(preview.missing_required) == set(REQUIRED_FIELDS)
        assert preview.can_import is False

    def test_a_complete_mapping_can_import(self):
        preview = preview_csv(CLEAN)
        assert preview.missing_required == []
        assert preview.can_import is True

    def test_an_empty_file_is_not_a_crash(self):
        preview = preview_csv("")
        assert preview.total_rows == 0
        assert preview.can_import is False

    def test_a_header_only_file_is_not_importable(self):
        assert preview_csv("name,email\n").can_import is False

    def test_utf8_bom_is_stripped(self):
        """Excel exports carry a BOM that otherwise corrupts the first header."""
        preview = preview_csv("\ufeffname,email\nA,a@x.com\n")
        assert preview.headers[0] == "name"
        assert preview.mapping["name"] == "full_name"

    def test_semicolon_delimited_files_are_handled(self):
        """European Excel exports use semicolons."""
        preview = preview_csv("name;email\nDana;dana@example.com\n")
        assert preview.headers == ["name", "email"]
        assert preview.total_rows == 1


class TestApplyRoster:
    def test_imports_every_valid_row(self, conn, event):
        outcome = apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        assert outcome.imported == 3
        assert len(repo.list_attendees(conn, event)) == 3

    def test_stores_the_mapped_fields(self, conn, event):
        apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        dana = next(a for a in repo.list_attendees(conn, event)
                    if a.email == "dana@example.com")
        assert dana.full_name == "Dana Reyes"
        assert dana.title == "Program Director"
        assert dana.company == "Saronic"

    def test_vip_column_is_interpreted(self, conn, event):
        apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        by_email = {a.email: a for a in repo.list_attendees(conn, event)}
        assert by_email["dana@example.com"].is_vip is True
        assert by_email["sam@example.com"].is_vip is False
        assert by_email["ada@example.com"].is_vip is False

    def test_bad_rows_are_skipped_not_fatal(self, conn, event):
        outcome = apply_roster(conn, event, MESSY, preview_csv(MESSY).mapping)
        assert outcome.imported == 2
        assert len(outcome.skipped) == 3

    def test_each_skipped_row_says_which_line_and_why(self, conn, event):
        outcome = apply_roster(conn, event, MESSY, preview_csv(MESSY).mapping)
        lines = {s.line for s in outcome.skipped}
        assert lines == {3, 4, 5}          # 1 is the header
        assert any("name" in s.reason.lower() for s in outcome.skipped)
        assert any("email" in s.reason.lower() for s in outcome.skipped)

    def test_a_duplicate_email_is_not_imported_twice(self, conn, event):
        apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        outcome = apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        assert outcome.imported == 0
        assert outcome.duplicates == 3
        assert len(repo.list_attendees(conn, event)) == 3

    def test_re_importing_a_grown_file_adds_only_the_new_people(self, conn, event):
        apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        grown = CLEAN + "New Person,new@example.com,Analyst,Acme,no\n"
        outcome = apply_roster(conn, event, grown, preview_csv(grown).mapping)
        assert outcome.imported == 1
        assert len(repo.list_attendees(conn, event)) == 4

    def test_an_erased_persons_email_does_not_block_reimport(self, conn, event):
        """Erasure destroys the email, so it cannot be matched against."""
        apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        target = next(a for a in repo.list_attendees(conn, event)
                      if a.email == "dana@example.com")
        repo.erase_attendee(conn, target.id)
        outcome = apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        assert outcome.imported == 1

    def test_ignored_columns_are_not_stored(self, conn, event):
        csv_text = "name,email,dietary\nDana,dana@example.com,vegan\n"
        mapping = preview_csv(csv_text).mapping
        assert mapping["dietary"] == "ignore"
        apply_roster(conn, event, csv_text, mapping)
        row = dict(conn.execute("SELECT * FROM attendees").fetchone())
        assert "vegan" not in " ".join(str(v) for v in row.values() if v)

    def test_importing_without_a_required_mapping_refuses(self, conn, event):
        with pytest.raises(ValueError, match="email"):
            apply_roster(conn, event, CLEAN, {"Full Name": "full_name"})

    def test_whitespace_is_trimmed(self, conn, event):
        csv_text = "name,email\n  Dana Reyes  ,  dana@example.com \n"
        apply_roster(conn, event, csv_text, preview_csv(csv_text).mapping)
        person = repo.list_attendees(conn, event)[0]
        assert person.full_name == "Dana Reyes"
        assert person.email == "dana@example.com"

    def test_imported_attendees_are_not_marked_attended(self, conn, event):
        """Importing a roster invites people; it does not check them in."""
        apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        assert all(a.attended_at is None for a in repo.list_attendees(conn, event))

    def test_imported_attendees_are_not_self_reported(self, conn, event):
        apply_roster(conn, event, CLEAN, preview_csv(CLEAN).mapping)
        assert all(a.self_reported is False for a in repo.list_attendees(conn, event))


class TestRosterUi:
    """The three design rules, pinned."""

    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    @pytest.fixture()
    def eid(self, client) -> int:
        r = client.post("/events", data={"name": "E", "city": "Austin"},
                        follow_redirects=False)
        return int(r.headers["location"].rstrip("/").split("/")[2])

    def _upload(self, client, eid, text):
        return client.post(f"/events/{eid}/roster/preview",
                           files={"roster": ("roster.csv", text, "text/csv")})

    def test_upload_shows_a_preview_without_importing(self, client, eid):
        page = self._upload(client, eid, CLEAN)
        assert page.status_code == 200
        assert "Dana Reyes" in page.text
        assert "On the roster (0)" in page.text

    def test_the_button_states_the_real_row_count(self, client, eid):
        page = self._upload(client, eid, CLEAN)
        assert "Import 3 attendees" in page.text

    def test_unmapped_required_fields_block_the_button(self, client, eid):
        page = self._upload(client, eid, "Company,Title\nAcme,Engineer\n")
        assert "disabled" in page.text
        assert "pending-note" in page.text

    def test_a_mappable_file_does_not_disable_the_button(self, client, eid):
        assert "disabled" not in self._upload(client, eid, CLEAN).text

    def test_importing_reports_skipped_rows_after_the_fact(self, client, eid):
        self._upload(client, eid, MESSY)
        mapping = preview_csv(MESSY).mapping
        data = {"csv_text": MESSY}
        data.update({f"map_{h}": f for h, f in mapping.items()})
        page = client.post(f"/events/{eid}/roster/import", data=data)
        assert "2 attendees imported" in page.text
        assert "3 rows skipped" in page.text
        assert "Row 3" in page.text

    def test_the_coordinator_can_override_a_guessed_mapping(self, client, eid):
        csv_text = "person,contact\nDana Reyes,dana@example.com\n"
        self._upload(client, eid, csv_text)
        page = client.post(f"/events/{eid}/roster/import", data={
            "csv_text": csv_text,
            "map_person": "full_name",
            "map_contact": "email",
        })
        assert "1 attendee imported" in page.text

    def test_vips_are_marked_on_the_roster(self, client, eid):
        mapping = preview_csv(CLEAN).mapping
        data = {"csv_text": CLEAN}
        data.update({f"map_{h}": f for h, f in mapping.items()})
        page = client.post(f"/events/{eid}/roster/import", data=data)
        assert "fit-vip" in page.text
