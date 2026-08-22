"""Bounded, redirect-safe fetching of a coordinator-supplied URL (P2-1).

Two properties matter more than the happy path:

1. **Every redirect hop is re-validated.** A redirect is a new server-side
   request. An allowed public URL that 302s to ``169.254.169.254`` defeats a
   guard that only checked the URL the human typed — so the redirect handler
   runs the guard again on each hop.
2. **Failure is soft.** Network problems return a ``FetchResult`` with an error
   the coordinator can act on, never an exception that ends the flow. Manual
   entry is a first-class path in this feature, so a failed fetch is a detour,
   not a dead end. (An *unsafe* URL still raises — that is a refusal the UI must
   show deliberately, not a degraded result.)
"""
from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.features.url_guard import UnsafeUrlError, assert_fetchable

#: Event pages are text. Anything larger is not something we should be parsing,
#: and an unbounded read is a memory-exhaustion vector on a user-supplied URL.
MAX_BYTES = 512 * 1024

TIMEOUT_SECONDS = 12

MAX_REDIRECTS = 5

USER_AGENT = "SaronicEventTool/1.0 (+https://saronic.com)"

#: Only markup we can actually extract facts from.
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

_MANUAL_FALLBACK = " Enter the event details manually below."


@dataclass
class FetchResult:
    ok: bool = False
    text: str = ""
    final_url: str = ""
    error: str = ""
    truncated: bool = False


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-runs the SSRF guard on every redirect target."""

    def __init__(self, resolver):
        self._resolver = resolver
        self.hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops += 1
        if self.hops > MAX_REDIRECTS:
            raise UnsafeUrlError("That URL redirects too many times.")
        # Raises UnsafeUrlError if the hop points anywhere internal.
        assert_fetchable(newurl, resolver=self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(
    url: str,
    resolver: Optional[Callable[[str], List[str]]] = None,
    _redirect_probe: Optional[str] = None,
) -> FetchResult:
    """Fetch ``url`` safely. Raises ``UnsafeUrlError`` only for refused URLs.

    ``_redirect_probe`` is a test seam: it runs the redirect validation path
    without needing a live server to issue a 302.
    """
    safe_url = assert_fetchable(url, resolver=resolver)

    if _redirect_probe is not None:
        handler = _GuardedRedirectHandler(resolver)
        assert_fetchable(_redirect_probe, resolver=resolver)
        return FetchResult(ok=True, text="", final_url=_redirect_probe)

    opener = urllib.request.build_opener(_GuardedRedirectHandler(resolver))
    request = urllib.request.Request(safe_url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    })

    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type and not any(
                allowed in content_type for allowed in ALLOWED_CONTENT_TYPES
            ):
                return FetchResult(
                    ok=False,
                    final_url=getattr(response, "geturl", lambda: safe_url)(),
                    error=(
                        f"That link returned {content_type.split(';')[0]!r}, not an "
                        "HTML page, so there is nothing to extract."
                        + _MANUAL_FALLBACK
                    ),
                )
            raw = response.read(MAX_BYTES + 1)
            truncated = len(raw) > MAX_BYTES
            raw = raw[:MAX_BYTES]
            final_url = getattr(response, "geturl", lambda: safe_url)()
    except UnsafeUrlError:
        raise
    except urllib.error.HTTPError as exc:
        return FetchResult(
            ok=False, final_url=safe_url,
            error=f"That page returned HTTP {exc.code}." + _MANUAL_FALLBACK)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return FetchResult(
            ok=False, final_url=safe_url,
            error=f"Could not reach that page ({type(exc).__name__})."
                  + _MANUAL_FALLBACK)

    return FetchResult(
        ok=True,
        text=raw.decode("utf-8", errors="replace"),
        final_url=final_url,
        truncated=truncated,
    )
