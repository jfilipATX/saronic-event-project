"""Slides + check-in screens in the workflow shell.

Both hang off the same event shell as the decision steps, so the stepper stays
coherent: slides are derived from the playbook, check-in is operated on the day.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(db_path=str(tmp_path / "t.db")))


@pytest.fixture()
def event_id(client) -> int:
    r = client.post("/events", data={"name": "Saronic Fleet Week", "city": "Austin"},
                    follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


@pytest.fixture()
def planned(client, event_id) -> int:
    for step, key in (("event_type", "convention"), ("audience", "conservative"),
                      ("venue", "austin-convention-center")):
        client.post(f"/events/{event_id}/decide", data={"step": step, "key": key})
    return event_id


class TestSlidesScreen:
    def test_slides_page_renders_the_deck(self, client, planned):
        r = client.get(f"/events/{planned}/slides")
        assert r.status_code == 200
        assert "Saronic Fleet Week" in r.text

    def test_every_deck_slide_appears(self, client, planned):
        r = client.get(f"/events/{planned}/slides")
        for kind in ("title", "decision", "closing"):
            assert kind in r.text

    def test_brand_images_render_by_role_not_filename(self, client, planned):
        r = client.get(f"/events/{planned}/slides")
        assert "hero-16x9" in r.text
        assert "logo-on-dark" in r.text

    def test_draft_deck_is_labelled_in_the_ui(self, client, event_id):
        r = client.get(f"/events/{event_id}/slides")
        assert "Draft" in r.text or "DRAFT" in r.text

    def test_complete_deck_is_not_labelled_draft(self, client, planned):
        r = client.get(f"/events/{planned}/slides")
        assert "DRAFT" not in r.text

    def test_markdown_outline_export(self, client, planned):
        r = client.get(f"/events/{planned}/slides.md")
        assert r.status_code == 200
        assert r.text.startswith("# Deck")

    def test_unknown_event_404s(self, client):
        assert client.get("/events/9999/slides").status_code == 404


class TestCheckinScreen:
    def test_checkin_page_renders(self, client, planned):
        r = client.get(f"/events/{planned}/checkin")
        assert r.status_code == 200
        assert "check-in" in r.text.lower()

    def test_valid_scan_marks_attendance_and_reports_valid(self, client, planned):
        import app.main as main_mod
        code = main_mod.issue_invite(main_mod.CURRENT_DB, planned, "Dana Reyes",
                                     "dana@example.com")
        r = client.post(f"/events/{planned}/checkin", data={"code": code})
        assert r.status_code == 200
        assert "Dana Reyes" in r.text
        assert "scan-valid" in r.text

    def test_replayed_scan_is_reported_as_already(self, client, planned):
        import app.main as main_mod
        code = main_mod.issue_invite(main_mod.CURRENT_DB, planned, "Dana Reyes")
        client.post(f"/events/{planned}/checkin", data={"code": code})
        r = client.post(f"/events/{planned}/checkin", data={"code": code})
        assert "scan-already" in r.text

    def test_forged_scan_is_reported_as_tampered(self, client, planned):
        r = client.post(f"/events/{planned}/checkin",
                        data={"code": "1.1700000000." + "f" * 64})
        assert "scan-tampered" in r.text

    def test_empty_scan_rerenders_desk_not_422(self, client, planned):
        # A fat-fingered empty scan must give the door operator the tampered
        # banner on the same page, never FastAPI's raw 422 JSON.
        for payload in ({"code": ""}, {"code": "   "}, {}):
            r = client.post(f"/events/{planned}/checkin", data=payload)
            assert r.status_code == 200
            assert "scan-tampered" in r.text

    def test_walk_in_can_self_add(self, client, planned):
        """A walk-in now requires all four fields (P2-5)."""
        r = client.post(f"/events/{planned}/checkin/walkin",
                        data={"full_name": "Walk In", "email": "walk@example.com",
                              "title": "Analyst", "company": "Acme"},
                        follow_redirects=True)
        assert "Walk In" in r.text

    def test_an_incomplete_walk_in_is_refused_with_a_reason(self, client, planned):
        """The guest is standing there — the desk needs the reason, not a 400."""
        r = client.post(f"/events/{planned}/checkin/walkin",
                        data={"full_name": "Walk In"}, follow_redirects=True)
        assert r.status_code == 200
        assert "email" in r.text.lower()
        assert "Walk In" not in r.text.replace("Walk-in", "")

    def test_roster_distinguishes_verified_from_walk_in(self, client, planned):
        import app.main as main_mod
        code = main_mod.issue_invite(main_mod.CURRENT_DB, planned, "Dana Reyes")
        client.post(f"/events/{planned}/checkin", data={"code": code})
        client.post(f"/events/{planned}/checkin/walkin", data={"full_name": "Walk In"})
        r = client.get(f"/events/{planned}/checkin")
        assert "Verified" in r.text
        assert "Walk-in" in r.text

    def test_checkin_page_never_exposes_the_signing_secret(self, client, planned):
        body = client.get(f"/events/{planned}/checkin").text.lower()
        assert "signing_secret" not in body
        assert "event_signing_secret" not in body


class TestFactoryHonoursDbPathEnv:
    """uvicorn --factory calls create_app() with no args.

    A literal default here silently ignored $DB_PATH, so the server wrote to
    events.db while issue_invite() wrote to the configured database — invites
    scanned as 'tampered' for a reason that looked like a signing bug.
    """

    def test_create_app_with_no_args_uses_db_path_env(self, tmp_path, monkeypatch):
        import app.main as main_mod

        target = str(tmp_path / "from_env.db")
        monkeypatch.setenv("DB_PATH", target)
        main_mod.create_app()
        assert main_mod.CURRENT_DB == target

    def test_explicit_argument_still_wins(self, tmp_path, monkeypatch):
        import app.main as main_mod

        monkeypatch.setenv("DB_PATH", str(tmp_path / "env.db"))
        explicit = str(tmp_path / "explicit.db")
        main_mod.create_app(explicit)
        assert main_mod.CURRENT_DB == explicit

    def test_falls_back_to_events_db_when_unset(self, monkeypatch):
        import app.main as main_mod

        monkeypatch.delenv("DB_PATH", raising=False)
        main_mod.create_app()
        assert main_mod.CURRENT_DB == "events.db"
