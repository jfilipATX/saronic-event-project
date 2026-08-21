"""RealImageProvider — request shape and fail-soft behaviour.

No network here: the transport is stubbed so these run offline and in CI. The
live call was verified separately against Pexels.

The User-Agent assertion is a real regression guard: Pexels sits behind
Cloudflare, which returns 403 (error 1010) to urllib's default agent even with a
perfectly valid key. Dropping that header silently breaks every stock lookup.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from app.providers.real.providers import RealImageProvider


class _Cfg:
    def __init__(self, key="test-key"):
        self.pexels_api_key = key


def _response(payload: dict):
    class _Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp()


_PHOTOS = {
    "photos": [
        {"src": {"landscape": "https://images.pexels.com/a.jpg"},
         "alt": "Austin skyline", "photographer": "Someone"},
        {"src": {"large": "https://images.pexels.com/b.jpg"},
         "alt": "", "photographer": "Other"},
        {"src": {}, "alt": "no usable src"},
    ]
}


@pytest.fixture()
def captured(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
        seen["timeout"] = timeout
        return _response(_PHOTOS)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


class TestRequestShape:
    def test_sends_a_real_user_agent_not_the_urllib_default(self, captured):
        RealImageProvider(_Cfg()).fetch("Austin", "city-stock")
        ua = captured["headers"].get("user-agent", "")
        assert ua and "python-urllib" not in ua.lower(), (
            "Cloudflare 403s the default urllib agent regardless of key validity"
        )

    def test_sends_the_key_as_the_authorization_header(self, captured):
        RealImageProvider(_Cfg("secret-key")).fetch("Austin", "city-stock")
        assert captured["headers"].get("authorization") == "secret-key"

    def test_city_role_biases_the_query_toward_skylines(self, captured):
        RealImageProvider(_Cfg()).fetch("Austin", "city-stock")
        assert "skyline" in captured["url"]

    def test_request_is_bounded_by_a_timeout(self, captured):
        RealImageProvider(_Cfg()).fetch("Austin", "city-stock")
        assert captured["timeout"] and captured["timeout"] <= 15

    def test_per_page_is_clamped_to_the_api_maximum(self, captured):
        RealImageProvider(_Cfg()).fetch("Austin", "city-stock", limit=500)
        assert "per_page=15" in captured["url"]


class TestParsing:
    def test_returns_assets_for_usable_photos_only(self, captured):
        got = RealImageProvider(_Cfg()).fetch("Austin", "city-stock")
        assert [a.url for a in got] == [
            "https://images.pexels.com/a.jpg",
            "https://images.pexels.com/b.jpg",
        ]

    def test_role_is_carried_onto_each_asset(self, captured):
        got = RealImageProvider(_Cfg()).fetch("Austin", "city-stock")
        assert all(a.role == "city-stock" for a in got)

    def test_missing_alt_falls_back_to_a_useful_caption(self, captured):
        got = RealImageProvider(_Cfg()).fetch("Austin", "city-stock")
        assert "Austin" in got[1].caption


class TestFailSoft:
    def test_no_key_means_no_call_and_no_error(self, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("must not call the API without a key")

        monkeypatch.setattr("urllib.request.urlopen", explode)
        assert RealImageProvider(_Cfg("")).fetch("Austin", "city-stock") == []

    def test_blank_query_makes_no_call(self, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("must not call the API with an empty query")

        monkeypatch.setattr("urllib.request.urlopen", explode)
        assert RealImageProvider(_Cfg()).fetch("   ", "city-stock") == []

    @pytest.mark.parametrize("exc", [
        urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"1010")),
        urllib.error.URLError("no route to host"),
        TimeoutError("slow"),
        OSError("socket blew up"),
    ])
    def test_network_failures_degrade_to_empty_not_an_exception(self, monkeypatch, exc):
        """A stock outage must never take down a deck built from owned assets."""
        def boom(*a, **k):
            raise exc

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert RealImageProvider(_Cfg()).fetch("Austin", "city-stock") == []

    def test_malformed_json_degrades_to_empty(self, monkeypatch):
        class _Bad:
            def read(self):
                return b"<html>not json</html>"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Bad())
        assert RealImageProvider(_Cfg()).fetch("Austin", "city-stock") == []
