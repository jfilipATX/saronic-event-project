"""P4-1 — add a venue by URL, with amenity extraction.

Reuses the P2-1 stack wholesale (SSRF guard, bounded fetcher, allowlist
extraction) with a venue-shaped field set. The rules carried over matter more
than the new fields:

* **Scraped values are proposals, never facts.** Every extracted field becomes a
  prefilled, editable option the coordinator confirms, attributed to its source.
* **The allowlist is iterated, not the model response** — an invented field
  cannot reach the venue record.

New here: amenities are a **closed checklist**, not free text. "Do they do
catering?" has three answers a coordinator can act on — yes, no, and *the page
doesn't say* — and collapsing the third into "no" turns a research gap into a
false negative someone plans around.
"""
from __future__ import annotations

import json

import pytest

from app.features.venue_scrape import (
    AMENITIES,
    VENUE_FIELDS,
    amenity_summary,
    build_venue_options,
    extract_venue,
    venue_from_facts,
)


class _Claude:
    """Stub whose reply is whatever the test wants."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, *, system, prompt, **kw):
        self.calls.append({"system": system, "prompt": prompt, **kw})
        return self.reply


_GOOD = json.dumps({
    "venue_name": "Palmer Events Center",
    "address": "900 Barton Springs Rd, Austin, TX 78704",
    "city": "Austin",
    "capacity": 3000,
    "website": "https://www.palmereventscenter.com",
    "amenities": {
        "catering": "yes",
        "alcohol_service": "yes",
        "security": "no",
        "av_production": "yes",
        "parking": "yes",
        "loading_access": "unknown",
        "wifi": "yes",
        "accessible": "unknown",
    },
})


class TestExtraction:
    def test_the_declared_fields_are_extracted(self):
        facts = extract_venue(_Claude(_GOOD), "<html/>", "https://palmer.test")
        assert facts["venue_name"] == "Palmer Events Center"
        assert facts["capacity"] == 3000
        assert "Barton Springs" in facts["address"]

    def test_an_invented_field_cannot_get_through(self):
        reply = json.dumps({"venue_name": "X", "nightly_rate": "$9,000",
                            "amenities": {}})
        facts = extract_venue(_Claude(reply), "<html/>", "https://x.test")
        assert "nightly_rate" not in facts

    def test_every_key_is_a_declared_field(self):
        facts = extract_venue(_Claude(_GOOD), "<html/>", "https://x.test")
        assert set(facts) <= set(VENUE_FIELDS) | {"amenities"}

    def test_a_missing_field_is_absent_rather_than_guessed(self):
        reply = json.dumps({"venue_name": "Sparse Hall", "amenities": {}})
        facts = extract_venue(_Claude(reply), "<html/>", "https://x.test")
        assert "capacity" not in facts

    def test_unparseable_output_yields_nothing_not_an_exception(self):
        assert extract_venue(_Claude("I could not read that page."),
                             "<html/>", "https://x.test") == {}

    def test_no_client_yields_nothing(self):
        assert extract_venue(None, "<html/>", "https://x.test") == {}

    def test_a_non_numeric_capacity_is_dropped(self):
        reply = json.dumps({"venue_name": "X", "capacity": "about 3000ish",
                            "amenities": {}})
        assert "capacity" not in extract_venue(_Claude(reply), "<h/>", "https://x.test")

    def test_the_source_url_is_given_to_the_model(self):
        client = _Claude(_GOOD)
        extract_venue(client, "<html/>", "https://palmer.test/venue")
        assert "palmer.test" in client.calls[0]["prompt"]

    def test_the_call_is_attributed_for_the_spend_ledger(self):
        client = _Claude(_GOOD)
        extract_venue(client, "<html/>", "https://x.test", event_id=7)
        assert client.calls[0]["surface"] == "venue_scrape"
        assert client.calls[0]["event_id"] == 7


class TestAmenities:
    def test_every_amenity_is_reported_even_when_the_page_is_silent(self):
        """A closed checklist: absence of evidence is its own answer."""
        reply = json.dumps({"venue_name": "Quiet Hall",
                            "amenities": {"catering": "yes"}})
        facts = extract_venue(_Claude(reply), "<h/>", "https://x.test")
        assert set(facts["amenities"]) == set(AMENITIES)

    def test_an_unmentioned_amenity_is_unknown_not_no(self):
        reply = json.dumps({"venue_name": "Quiet Hall",
                            "amenities": {"catering": "yes"}})
        facts = extract_venue(_Claude(reply), "<h/>", "https://x.test")
        assert facts["amenities"]["security"] == "unknown"

    def test_an_explicit_no_survives(self):
        facts = extract_venue(_Claude(_GOOD), "<h/>", "https://x.test")
        assert facts["amenities"]["security"] == "no"

    def test_an_invented_amenity_is_discarded(self):
        reply = json.dumps({"venue_name": "X",
                            "amenities": {"helipad": "yes", "catering": "yes"}})
        facts = extract_venue(_Claude(reply), "<h/>", "https://x.test")
        assert "helipad" not in facts["amenities"]

    def test_a_junk_value_becomes_unknown(self):
        reply = json.dumps({"venue_name": "X",
                            "amenities": {"catering": "maybe on Tuesdays"}})
        facts = extract_venue(_Claude(reply), "<h/>", "https://x.test")
        assert facts["amenities"]["catering"] == "unknown"

    def test_the_summary_names_what_is_missing_as_a_cost(self):
        """A gap is planning information, not a blank."""
        text = amenity_summary({"catering": "yes", "security": "no",
                                "alcohol_service": "unknown"})
        assert "security" in text.lower()
        assert "not stated" in text.lower() or "unknown" in text.lower()

    def test_the_summary_is_honest_when_nothing_is_known(self):
        text = amenity_summary({k: "unknown" for k in AMENITIES})
        assert "nothing" in text.lower() or "no amenities" in text.lower()


class TestBecomingAVenue:
    def test_a_venue_is_built_from_confirmed_facts(self):
        venue = venue_from_facts({
            "venue_name": "Palmer Events Center", "city": "Austin",
            "capacity": 3000, "website": "https://palmer.test",
            "amenities": {"catering": "yes"},
        }, source_url="https://palmer.test")
        assert venue.name == "Palmer Events Center"
        assert venue.capacity == 3000
        assert venue.website == "https://palmer.test"

    def test_a_scraped_venue_gets_a_stable_ref(self):
        venue = venue_from_facts({"venue_name": "Palmer", "city": "Austin",
                                  "capacity": 3000},
                                 source_url="https://palmer.test")
        assert venue.venue_ref

    def test_the_ref_is_stable_across_a_rename(self):
        """Favourites and history key on this, so the URL anchors it."""
        a = venue_from_facts({"venue_name": "Palmer", "city": "Austin",
                              "capacity": 3000}, source_url="https://palmer.test")
        b = venue_from_facts({"venue_name": "Palmer Events Center",
                              "city": "Austin", "capacity": 3000},
                             source_url="https://palmer.test")
        assert a.venue_ref == b.venue_ref

    def test_a_venue_without_a_capacity_is_refused(self):
        """Capacity drives the fit badge — a venue without one cannot be rated."""
        with pytest.raises(ValueError, match="capacity"):
            venue_from_facts({"venue_name": "X", "city": "Austin"},
                             source_url="https://x.test")

    def test_the_notes_carry_the_amenity_summary(self):
        venue = venue_from_facts({
            "venue_name": "X", "city": "Austin", "capacity": 3000,
            "amenities": {"security": "no", "catering": "yes"},
        }, source_url="https://x.test")
        assert "security" in venue.notes.lower()


class TestConfirmOptions:
    def test_each_field_becomes_a_confirmable_option(self):
        facts = extract_venue(_Claude(_GOOD), "<h/>", "https://palmer.test")
        options = build_venue_options(facts, "https://palmer.test")
        keys = {o.key for o in options}
        assert "venue_name" in keys and "capacity" in keys

    def test_the_reasoning_attributes_the_source(self):
        facts = extract_venue(_Claude(_GOOD), "<h/>", "https://palmer.test")
        option = next(o for o in build_venue_options(facts, "https://palmer.test")
                      if o.key == "venue_name")
        assert "palmer.test" in option.reasoning

    def test_the_scraped_value_is_the_prefilled_default(self):
        facts = extract_venue(_Claude(_GOOD), "<h/>", "https://palmer.test")
        option = next(o for o in build_venue_options(facts, "https://palmer.test")
                      if o.key == "capacity")
        assert option.data["value"] == 3000

    def test_amenities_become_one_option_per_amenity(self):
        facts = extract_venue(_Claude(_GOOD), "<h/>", "https://palmer.test")
        options = build_venue_options(facts, "https://palmer.test")
        amenity_options = [o for o in options if o.key.startswith("amenity_")]
        assert len(amenity_options) == len(AMENITIES)

    def test_an_unknown_amenity_reads_as_a_question_not_a_denial(self):
        facts = extract_venue(_Claude(_GOOD), "<h/>", "https://palmer.test")
        option = next(o for o in build_venue_options(facts, "https://palmer.test")
                      if o.key == "amenity_loading_access")
        assert "not stated" in option.reasoning.lower()


class TestVenueAddUi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    @pytest.fixture()
    def eid(self, client):
        r = client.post("/events", data={"name": "Fleet Week", "city": "Austin"},
                        follow_redirects=False)
        return int(r.headers["location"].rstrip("/").split("/")[2])

    def test_the_venue_step_offers_adding_by_url(self, client, eid):
        # The venue step must actually be staged: on a fresh event it is not,
        # and the route redirects to the pending question instead.
        for step, key in (("event_type", "convention"), ("audience", "baseline")):
            client.post(f"/events/{eid}/decide", data={"step": step, "key": key})
        assert "venues/add" in client.get(f"/events/{eid}/steps/venue").text

    def test_the_add_form_renders_with_every_amenity(self, client, eid):
        page = client.get(f"/events/{eid}/venues/add").text
        for key in AMENITIES:
            assert f"amenity_{key}" in page

    def test_an_unsafe_url_is_refused_with_a_route_onward(self, client, eid):
        page = client.post(f"/events/{eid}/venues/scrape",
                           data={"venue_url": "http://169.254.169.254/"}).text
        assert "safely" in page.lower()
        assert "manually" in page.lower()

    def test_a_manual_venue_joins_the_slate(self, client, eid):
        client.post(f"/events/{eid}/venues/add", data={
            "venue_name": "Saronic Yard", "city": "Austin", "capacity": "2500",
            "source_url": "https://yard.test",
        }, follow_redirects=False)
        assert "Saronic Yard" in client.get(f"/events/{eid}/steps/venue").text

    def test_a_venue_without_capacity_is_refused(self, client, eid):
        r = client.post(f"/events/{eid}/venues/add", data={
            "venue_name": "No Capacity", "city": "Austin",
            "source_url": "https://x.test"})
        assert r.status_code == 400
        assert "capacity" in r.text.lower()

    def test_the_added_venue_is_rated_like_any_other(self, client, eid):
        """It must get a fit badge, not sit in a separate list."""
        for step, key in (("event_type", "convention"), ("audience", "baseline")):
            client.post(f"/events/{eid}/decide", data={"step": step, "key": key})
        client.post(f"/events/{eid}/venues/add", data={
            "venue_name": "Saronic Yard", "city": "Austin", "capacity": "2500",
            "source_url": "https://yard.test"}, follow_redirects=False)
        page = client.get(f"/events/{eid}/steps/venue").text
        assert "Saronic Yard" in page and "fit-" in page

    def test_amenities_reach_the_venue_notes(self, client, eid):
        client.post(f"/events/{eid}/venues/add", data={
            "venue_name": "Saronic Yard", "city": "Austin", "capacity": "2500",
            "source_url": "https://yard.test", "amenity_security": "no",
        }, follow_redirects=False)
        assert "security" in client.get(f"/events/{eid}/steps/venue").text.lower()

    def test_the_confirm_page_renders_with_populated_options(self, client, eid,
                                                             monkeypatch):
        """Renders the POPULATED branch, which the empty-slate tests never hit.

        A live scrape 500'd on `selectattr('key','match',...)` — Jinja has no
        'match' test — while every existing test passed, because they all
        exercised the empty-options path where the loop never runs.
        """
        import app.main as main_mod

        monkeypatch.setattr(main_mod, "_scrape_client",
                            lambda conn=None: _Claude(_GOOD))

        class _Result:
            text = "<html>venue</html>"
            final_url = "https://palmer.test/venue"

        monkeypatch.setattr(main_mod, "fetch_url", lambda url: _Result())
        page = client.post(f"/events/{eid}/venues/scrape",
                           data={"venue_url": "https://palmer.test/venue"})
        assert page.status_code == 200
        assert "Palmer Events Center" in page.text
        assert "amenity_security" in page.text
        assert "Confirm what we read" in page.text
