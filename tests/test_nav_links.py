"""Every stepper link must resolve — clicked, not just visited.

Regression guard for the Visuals nav bug (af5227f): the nav builder listed a
derived view the URL branch didn't know about, so the sidebar emitted a
decision-chain URL (`/steps/visuals`) that route-mismatched into another page.
None of the existing tests caught it because they navigated directly to known
URLs; nobody ever followed the links the UI actually renders.

These tests extract the hrefs from the *rendered sidebar* and follow each one,
so a future nav entry whose URL branch is missing fails here instead of in a
coordinator's browser. The assertion is deliberately on the rendered HTML, not
on `_NAV` — the contract is "what the user can click works," not "the config
looks right."
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(db_path=str(tmp_path / "t.db")))


@pytest.fixture()
def event_id(client) -> int:
    r = client.post("/events", data={"name": "Nav Walk", "city": "Austin"},
                    follow_redirects=False)
    return int(r.headers["location"].rstrip("/").split("/")[2])


def _stepper_hrefs(html: str) -> list[str]:
    """Hrefs inside the stepper list only — brand link and body links excluded."""
    stepper = re.search(r'<ul class="stepper">(.*?)</ul>', html, re.S)
    assert stepper, "stepper not found in rendered page"
    return re.findall(r'href="([^"]+)"', stepper.group(1))


class TestEveryStepperLinkResolves:
    def test_fresh_event_nav_links_all_resolve(self, client, event_id):
        html = client.get(f"/events/{event_id}/steps/event_type").text
        hrefs = _stepper_hrefs(html)
        assert len(hrefs) >= 7, f"expected the full nav, got {hrefs}"
        for href in hrefs:
            r = client.get(href, follow_redirects=True)
            assert r.status_code == 200, f"stepper link {href} -> {r.status_code}"

    def test_nav_links_resolve_from_every_page_they_appear_on(self, client, event_id):
        """The nav renders on many pages; a bad URL is a bad URL on all of them."""
        html = client.get(f"/events/{event_id}/checkin").text
        for href in _stepper_hrefs(html):
            r = client.get(href, follow_redirects=True)
            assert r.status_code == 200, f"stepper link {href} -> {r.status_code}"

    def test_each_nav_link_lands_on_its_own_page(self, client, event_id):
        """A link that 200s onto the WRONG page (the af5227f failure shape --
        /steps/visuals resolving into route-mismatch handling) must also fail.
        Every derived-view link must land on a page whose <h1>/<title> matches
        its label rather than another feature's."""
        html = client.get(f"/events/{event_id}/steps/event_type").text
        stepper = re.search(r'<ul class="stepper">(.*?)</ul>', html, re.S).group(1)
        links = re.findall(r'href="([^"]+)"[^>]*>([^<]+)<', stepper)
        # Derived from the app's own CHAIN rather than hand-listed. A literal
        # dict silently SKIPS any nav entry nobody remembered to add, which is
        # how this guard passed while the Schedule link was genuinely broken --
        # the same shape as the bug it was written to catch.
        from app.features.workflow import CHAIN
        from app.main import _NAV

        derived = {label: key for key, label in _NAV if key not in set(CHAIN)}
        seen = 0
        for href, label in links:
            label = label.strip()
            if label not in derived:
                continue
            seen += 1
            assert derived[label] in href, (
                f"nav label {label!r} points at {href!r} — wrong URL shape")
            page = client.get(href, follow_redirects=True).text
            # The page must self-identify: its title carries the label.
            assert re.search(rf"<title>[^<]*{re.escape(label.split()[0])}",
                             page, re.I), (
                f"{label!r} link landed on a page that doesn't identify as it")
        assert seen == len(derived), f"expected all derived views in nav, saw {seen}"
