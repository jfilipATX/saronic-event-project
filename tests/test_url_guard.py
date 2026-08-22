"""P2-1 — SSRF guard for coordinator-supplied event URLs.

Fetching a URL a user typed is a server-side request on their behalf. Without
constraints that is a hole straight into anything the host can reach: cloud
metadata endpoints (169.254.169.254), localhost admin panels, other containers
on the LXC bridge, and internal RFC1918 addresses.

These tests are the security boundary. They run offline — DNS resolution is
stubbed — because a guard that only works when the network agrees is not a guard.
"""
from __future__ import annotations

import pytest

from app.features.url_guard import (
    UnsafeUrlError,
    assert_fetchable,
    is_private_address,
)


class TestSchemeRules:
    @pytest.mark.parametrize("url", [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://example.com",
        "data:text/html,<h1>hi</h1>",
        "javascript:alert(1)",
        "jar:http://example.com!/",
    ])
    def test_non_http_schemes_are_rejected(self, url):
        with pytest.raises(UnsafeUrlError, match="scheme"):
            assert_fetchable(url, resolver=lambda h: ["93.184.216.34"])

    def test_http_and_https_are_allowed(self):
        for url in ("http://example.com/e", "https://example.com/e"):
            assert_fetchable(url, resolver=lambda h: ["93.184.216.34"])

    @pytest.mark.parametrize("url", ["", "   ", "not a url", "http://", "https:///path"])
    def test_malformed_input_is_rejected(self, url):
        with pytest.raises(UnsafeUrlError):
            assert_fetchable(url, resolver=lambda h: ["93.184.216.34"])


class TestPrivateAddressDetection:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1", "127.1.2.3",          # loopback
        "10.0.0.5", "172.16.4.9", "192.168.1.1",   # RFC1918
        "169.254.169.254",                  # cloud metadata — the classic target
        "0.0.0.0", "255.255.255.255",
        "::1", "fc00::1", "fe80::1",        # IPv6 loopback / ULA / link-local
        "100.64.0.1",                       # carrier-grade NAT
    ])
    def test_private_and_reserved_addresses_are_detected(self, ip):
        assert is_private_address(ip) is True

    @pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1::1"])
    def test_public_addresses_are_not_flagged(self, ip):
        assert is_private_address(ip) is False


class TestHostResolutionIsChecked:
    def test_a_hostname_resolving_to_loopback_is_rejected(self):
        """The DNS-rebinding shape: an innocuous name pointing inward."""
        with pytest.raises(UnsafeUrlError, match="private"):
            assert_fetchable("https://evil.example.com/e",
                             resolver=lambda h: ["127.0.0.1"])

    def test_metadata_endpoint_by_name_is_rejected(self):
        with pytest.raises(UnsafeUrlError, match="private"):
            assert_fetchable("http://metadata.internal/latest",
                             resolver=lambda h: ["169.254.169.254"])

    def test_any_private_answer_rejects_even_when_others_are_public(self):
        """A multi-record answer must fail closed, not pick the public one."""
        with pytest.raises(UnsafeUrlError, match="private"):
            assert_fetchable("https://mixed.example.com/e",
                             resolver=lambda h: ["93.184.216.34", "10.0.0.5"])

    def test_literal_private_ip_in_the_url_is_rejected(self):
        with pytest.raises(UnsafeUrlError, match="private"):
            assert_fetchable("http://169.254.169.254/latest/meta-data/",
                             resolver=lambda h: ["169.254.169.254"])

    def test_unresolvable_host_is_rejected(self):
        def boom(host):
            raise OSError("NXDOMAIN")

        with pytest.raises(UnsafeUrlError, match="resolve"):
            assert_fetchable("https://nope.example.com/e", resolver=boom)

    def test_empty_resolution_is_rejected(self):
        with pytest.raises(UnsafeUrlError, match="resolve"):
            assert_fetchable("https://nope.example.com/e", resolver=lambda h: [])


class TestPortRules:
    def test_default_ports_are_allowed(self):
        assert_fetchable("https://example.com/e", resolver=lambda h: ["93.184.216.34"])

    @pytest.mark.parametrize("port", [22, 25, 3306, 5432, 6379, 8080, 11211])
    def test_non_web_ports_are_rejected(self, port):
        with pytest.raises(UnsafeUrlError, match="port"):
            assert_fetchable(f"https://example.com:{port}/e",
                             resolver=lambda h: ["93.184.216.34"])

    def test_explicit_web_ports_are_allowed(self):
        for port in (80, 443):
            assert_fetchable(f"https://example.com:{port}/e",
                             resolver=lambda h: ["93.184.216.34"])


class TestCredentialsAndShape:
    def test_userinfo_in_the_url_is_rejected(self):
        """user:pass@host is a credential-leak and filter-bypass vector."""
        with pytest.raises(UnsafeUrlError):
            assert_fetchable("https://user:pass@example.com/e",
                             resolver=lambda h: ["93.184.216.34"])

    def test_an_absurdly_long_url_is_rejected(self):
        with pytest.raises(UnsafeUrlError, match="long"):
            assert_fetchable("https://example.com/" + "a" * 5000,
                             resolver=lambda h: ["93.184.216.34"])

    def test_the_checked_url_is_returned_normalised(self):
        out = assert_fetchable("https://Example.COM/Event ",
                               resolver=lambda h: ["93.184.216.34"])
        assert out.startswith("https://example.com/")
