"""Application configuration.

Single source of truth for env-driven settings. Uses stdlib ``os.environ`` with a
best-effort ``.env`` load; if ``python-dotenv`` is unavailable the values simply
fall back to the environment (so tests run with zero third-party deps).

IMPORTANT (security gate): every secret here is *server-side only*. This module is
imported exclusively by backend code. Nothing in ``app/static`` or ``app/templates``
may import it — the browser/frontend never sees these values (see ``code_review_gate``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _load_dotenv() -> None:
    """Best-effort .env population. Never raises if the package is missing."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return
    # Load from a .env in the project root (parent of this file's dir).
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    try:
        load_dotenv(env_path, override=False)
    except Exception:
        pass


_load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # ── Claude / Anthropic (server-side only) ──
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-20250514"
    anthropic_spend_limit: float = 250.0
    # P5-4: hard ceiling on Claude spend *for the venue-search surface alone*,
    # per event. Manual trigger must not be unbounded — once this event has
    # burned its venue-search budget, the button is refused pre-call.
    venue_search_cap_usd: float = 2.0

    # ── Provider mode ──
    provider_mode: str = "mock"          # "mock" | "real"
    use_real_claude: bool = False

    # ── Optional live APIs ──
    pexels_api_key: str = ""

    # ── QR signing (server-side only) ──
    event_signing_secret: str = ""

    # ── SQLite ──
    db_path: str = "events.db"

    @property
    def claude_enabled(self) -> bool:
        """Whether the real Claude API may be called. Mock mode is always safe.

        ``.strip()`` matters: a key that is whitespace (or a template line left
        as ``ANTHROPIC_API_KEY=`` with a trailing space) is a misconfiguration,
        not a credential. Treating it as enabled turns an offline test run into
        a slow network-dependent one, which is how this was found.
        """
        return self.use_real_claude and bool(self.anthropic_api_key.strip())


def load_config() -> Config:
    return Config(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-20250514"),
        anthropic_spend_limit=_env_float("ANTHROPIC_SPEND_LIMIT", 250.0),
        venue_search_cap_usd=_env_float("VENUE_SEARCH_CAP_USD", 2.0),
        provider_mode=os.environ.get("PROVIDER_MODE", "mock").strip().lower(),
        use_real_claude=_env_bool("USE_REAL_CLAUDE", False),
        pexels_api_key=os.environ.get("PEXELS_API_KEY", ""),
        event_signing_secret=os.environ.get("EVENT_SIGNING_SECRET", ""),
        db_path=os.environ.get("DB_PATH", "events.db"),
    )


# A module-level default so non-app code (tests, providers) can import cheaply.
CONFIG = load_config()
