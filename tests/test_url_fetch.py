"""P2-1 — the fetcher. Bounded, redirect-safe, and never a hard failure.

Every hop is re-validated: a redirect is a *new* server-side request, so an
allowed public URL that 302s to 169.254.169.254 is exactly the bypass the guard
exists to stop. Validating only the first URL is the classic mistake.

Everything degrades to "extraction failed, enter manually" — per the product
ruling, the coordinator is never stuck.
"""
from __future__ import annotations

import io
import urllib.error

import pytest

from app.features.url_fetch import (
    MAX_BYTES,
    FetchResult,
    fetch_url,
)
from app.features.url_guard import UnsafeUrlError

PUBLIC = lambda h: ["93.184.216.34"]  # noqa: E731


class _Resp:
    """Minimal stand-in for an http.client.HTTPResponse."""

    def __init__(self, body: bytes, status=200, headers=None, url=None):
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self._url = url or "https://example.com/e"

    def read(self, n=-1):
        return self._body.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(response):
    def _open(req, timeout=None):
        return response
    return _open


class TestSuccessfulFetch:
    def test_returns_the_body_and_final_url(self, monkeypatch):
        monkeypatch.setattr("urllib.request.OpenerDirector.open",
                            lambda self, req, timeout=None: _Resp(b"<h1>Event</h1>"))
        result = fetch_url("https://example.com/e", resolver=PUBLIC)
        assert result.ok is True
        assert "Event" in result.text
        assert result.final_url.startswith("https://example.com/")

    def test_decodes_utf8_content(self, monkeypatch):
        body = "<p>Rotterdam Ahoy — March 14–16</p>".encode("utf-8")
        monkeypatch.setattr("urllib.request.OpenerDirector.open",
                            lambda self, req, timeout=None: _Resp(body))
        result = fetch_url("https://example.com/e", resolver=PUBLIC)
        assert "Rotterdam Ahoy" in result.text

    def test_undecodable_bytes_do_not_raise(self, monkeypatch):
        monkeypatch.setattr("urllib.request.OpenerDirector.open",
                            lambda self, req, timeout=None: _Resp(b"\xff\xfe\x00bad"))
        result = fetch_url("https://example.com/e", resolver=PUBLIC)
        assert result.ok is True


class TestBounds:
    def test_oversized_body_is_truncated_not_loaded(self, monkeypatch):
        huge = b"x" * (MAX_BYTES * 3)
        monkeypatch.setattr("urllib.request.OpenerDirector.open",
                            lambda self, req, timeout=None: _Resp(huge))
        result = fetch_url("https://example.com/e", resolver=PUBLIC)
        assert len(result.text.encode()) <= MAX_BYTES
        assert result.truncated is True

    def test_normal_body_is_not_marked_truncated(self, monkeypatch):
        monkeypatch.setattr("urllib.request.OpenerDirector.open",
                            lambda self, req, timeout=None: _Resp(b"<p>small</p>"))
        assert fetch_url("https://example.com/e", resolver=PUBLIC).truncated is False

    def test_non_html_content_type_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.OpenerDirector.open",
            lambda self, req, timeout=None: _Resp(
                b"%PDF-1.4", headers={"Content-Type": "application/pdf"}))
        result = fetch_url("https://example.com/e.pdf", resolver=PUBLIC)
        assert result.ok is False
        assert "html" in result.error.lower()


class TestRedirectsAreRevalidated:
    def test_redirect_to_a_private_address_is_blocked(self):
        """The bypass this exists to stop: public URL -> 302 -> metadata IP."""
        def resolver(host):
            return ["93.184.216.34"] if host == "example.com" else ["169.254.169.254"]

        with pytest.raises(UnsafeUrlError):
            fetch_url("https://example.com/e", resolver=resolver,
                      _redirect_probe="http://metadata.internal/latest")

    def test_redirect_to_another_public_url_is_allowed(self):
        assert fetch_url("https://example.com/e", resolver=PUBLIC,
                         _redirect_probe="https://www.example.com/e2") is not None


class TestFailuresAreSoft:
    @pytest.mark.parametrize("exc", [
        urllib.error.HTTPError("u", 404, "Not Found", {}, None),
        urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
        urllib.error.URLError("no route"),
        TimeoutError("slow"),
        OSError("socket died"),
    ])
    def test_network_failures_return_a_result_not_an_exception(self, monkeypatch, exc):
        def boom(self, req, timeout=None):
            raise exc

        monkeypatch.setattr("urllib.request.OpenerDirector.open", boom)
        result = fetch_url("https://example.com/e", resolver=PUBLIC)
        assert result.ok is False
        assert result.error
        assert result.text == ""

    def test_the_error_tells_the_coordinator_what_to_do(self, monkeypatch):
        def boom(self, req, timeout=None):
            raise urllib.error.URLError("nope")

        monkeypatch.setattr("urllib.request.OpenerDirector.open", boom)
        result = fetch_url("https://example.com/e", resolver=PUBLIC)
        assert "manually" in result.error.lower()

    def test_an_unsafe_url_still_raises_for_the_caller_to_render(self):
        with pytest.raises(UnsafeUrlError):
            fetch_url("http://169.254.169.254/", resolver=lambda h: ["169.254.169.254"])


class TestRequestShape:
    def test_sends_a_real_user_agent(self, monkeypatch):
        seen = {}

        def capture(self, req, timeout=None):
            seen["ua"] = req.get_header("User-agent", "")
            seen["timeout"] = timeout
            return _Resp(b"<p>ok</p>")

        monkeypatch.setattr("urllib.request.OpenerDirector.open", capture)
        fetch_url("https://example.com/e", resolver=PUBLIC)
        assert seen["ua"] and "python-urllib" not in seen["ua"].lower()

    def test_request_is_bounded_by_a_timeout(self, monkeypatch):
        seen = {}

        def capture(self, req, timeout=None):
            seen["timeout"] = timeout
            return _Resp(b"<p>ok</p>")

        monkeypatch.setattr("urllib.request.OpenerDirector.open", capture)
        fetch_url("https://example.com/e", resolver=PUBLIC)
        assert seen["timeout"] and seen["timeout"] <= 15


class TestFetchResult:
    def test_failed_result_is_falsey_in_practice(self):
        assert FetchResult(ok=False, error="x").ok is False
