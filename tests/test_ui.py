"""T9-UI — the workflow shell as a real HTTP app.

Tests drive the app the way a coordinator does: create an event, walk the
stepper, choose options, revise, and read the playbook. Every assertion is
against real rendered HTML from a real request, not a template call.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    app = create_app(db_path=str(tmp_path / "test.db"))
    return TestClient(app)


@pytest.fixture()
def event_id(client) -> int:
    r = client.post("/events", data={"name": "Saronic Fleet Week", "city": "Austin"},
                    follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    return int(r.headers["location"].rstrip("/").split("/")[2])


class TestHomeAndCreate:
    def test_home_renders(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Saronic" in r.text

    def test_creating_an_event_redirects_into_the_workflow(self, client):
        r = client.post("/events", data={"name": "E", "city": "Austin"},
                        follow_redirects=False)
        assert r.status_code in (302, 303)
        assert "/events/" in r.headers["location"]

    def test_created_event_appears_on_home(self, client, event_id):
        assert "Saronic Fleet Week" in client.get("/").text


class TestStepPages:
    def test_first_step_is_event_type_and_lists_every_option(self, client, event_id):
        r = client.get(f"/events/{event_id}/steps/event_type")
        assert r.status_code == 200
        for label in ("Convention", "Panel", "Company-hosted event"):
            assert label in r.text

    def test_reasoning_is_visible_on_the_page_not_hidden(self, client, event_id):
        r = client.get(f"/events/{event_id}/steps/event_type")
        assert "Widest reach" in r.text

    def test_unstaged_step_is_not_a_server_error(self, client, event_id):
        r = client.get(f"/events/{event_id}/steps/venue")
        assert r.status_code in (200, 302, 303, 404)
        assert r.status_code != 500

    def test_unknown_event_404s(self, client):
        assert client.get("/events/9999/steps/event_type").status_code == 404


class TestDecideRoute:
    def test_choosing_records_the_decision_and_advances(self, client, event_id):
        r = client.post(f"/events/{event_id}/decide",
                        data={"step": "event_type", "key": "convention"},
                        follow_redirects=False)
        assert r.status_code in (302, 303)
        page = client.get(f"/events/{event_id}/steps/audience")
        assert page.status_code == 200
        assert "attendees" in page.text

    def test_chosen_option_is_marked_in_the_html(self, client, event_id):
        client.post(f"/events/{event_id}/decide",
                    data={"step": "event_type", "key": "convention"})
        r = client.get(f"/events/{event_id}/steps/event_type")
        assert "is-chosen" in r.text

    def test_venue_step_shows_fit_badges_after_audience(self, client, event_id):
        client.post(f"/events/{event_id}/decide",
                    data={"step": "event_type", "key": "convention"})
        client.post(f"/events/{event_id}/decide",
                    data={"step": "audience", "key": "baseline"})
        r = client.get(f"/events/{event_id}/steps/venue")
        assert "fit-fits" in r.text
        assert "fit-under" in r.text
        # Nothing hidden: all three Austin venues present.
        for name in ("Austin Convention Center", "Palmer Events Center", "Fairmont Austin"):
            assert name in r.text

    def test_reposting_the_chosen_key_revises_without_error(self, client, event_id):
        """Designer's 'Chosen stays enabled' undo path must not 500."""
        client.post(f"/events/{event_id}/decide",
                    data={"step": "event_type", "key": "convention"})
        r = client.post(f"/events/{event_id}/decide",
                        data={"step": "event_type", "key": "convention"},
                        follow_redirects=False)
        assert r.status_code in (302, 303)

    def test_choosing_an_unoffered_key_is_a_client_error_not_a_crash(self, client, event_id):
        r = client.post(f"/events/{event_id}/decide",
                        data={"step": "event_type", "key": "grand-ball"})
        assert r.status_code == 400

    def test_revising_upstream_restages_downstream_in_the_ui(self, client, event_id):
        for step, key in (("event_type", "convention"), ("audience", "baseline"),
                          ("venue", "austin-convention-center")):
            client.post(f"/events/{event_id}/decide", data={"step": step, "key": key})
        client.post(f"/events/{event_id}/decide",
                    data={"step": "audience", "key": "conservative"})
        r = client.get(f"/events/{event_id}/steps/venue")
        assert "is-chosen" not in r.text  # venue answer withdrawn, re-asked


class TestStepper:
    def test_stepper_shows_done_and_todo_states(self, client, event_id):
        client.post(f"/events/{event_id}/decide",
                    data={"step": "event_type", "key": "convention"})
        r = client.get(f"/events/{event_id}/steps/audience")
        assert "is-done" in r.text
        assert "is-active" in r.text


class TestPlaybookView:
    def test_playbook_renders_settled_decisions(self, client, event_id):
        for step, key in (("event_type", "convention"), ("audience", "conservative"),
                          ("venue", "austin-convention-center")):
            client.post(f"/events/{event_id}/decide", data={"step": step, "key": key})
        r = client.get(f"/events/{event_id}/playbook")
        assert r.status_code == 200
        assert "Austin Convention Center" in r.text
        assert "Also considered" in r.text

    def test_playbook_flags_open_questions(self, client, event_id):
        r = client.get(f"/events/{event_id}/playbook")
        assert "Open question" in r.text

    def test_markdown_export_returns_the_document(self, client, event_id):
        client.post(f"/events/{event_id}/decide",
                    data={"step": "event_type", "key": "convention"})
        r = client.get(f"/events/{event_id}/playbook.md")
        assert r.status_code == 200
        assert r.text.startswith("# Saronic Fleet Week")


class TestStaticAssets:
    def test_theme_css_is_served(self, client):
        r = client.get("/static/theme.css")
        assert r.status_code == 200
        assert "--color" in r.text

    def test_brand_logo_is_served(self, client):
        assert client.get("/static/brand/logo-on-dark.png").status_code == 200


class TestNoSecretsInHtml:
    def test_rendered_pages_never_contain_config_secrets(self, client, event_id):
        """The frontend must never see server-side config."""
        for path in ("/", f"/events/{event_id}/steps/event_type",
                     f"/events/{event_id}/playbook"):
            body = client.get(path).text.lower()
            for token in ("api_key", "anthropic_api_key", "pexels_api_key",
                          "signing_secret"):
                assert token not in body
