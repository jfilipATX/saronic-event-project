"""Test-wide safety net: the suite must never call a paid API.

Found when the suite slowed from 3s to 27s: the slides route calls real Claude
whenever ``.env`` carries a key and the flag is on, so simply *running the tests*
made live API calls, spent real money, and produced results that depended on
whose machine it ran on.

A suite that behaves differently based on a developer's ``.env`` is not a suite.
This forces mock mode for every test, unconditionally.

``monkeypatch.setenv`` alone is not enough: ``app.config`` loads ``.env`` at
import time with ``override=False``, so the file's values are already in
``os.environ`` before any fixture runs. Patching ``Config.claude_enabled`` closes
it at the only place that actually decides.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _never_call_real_claude(request, monkeypatch):
    """Force mock mode for the whole suite, regardless of local .env.

    Tests that legitimately assert the enabling logic opt out with
    ``@pytest.mark.real_claude_config`` — they check the property, they never
    make a call.
    """
    from app.config import Config

    monkeypatch.setenv("USE_REAL_CLAUDE", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if request.node.get_closest_marker("real_claude_config") is None:
        monkeypatch.setattr(Config, "claude_enabled", property(lambda self: False))
    yield


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_claude_config: test asserts claude_enabled logic; no API call is made.",
    )
