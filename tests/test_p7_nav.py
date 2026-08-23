"""P7-0 — global nav must not disappear on context-less pages.

The People page (/people) has no event context, so the stepper (rendered inside
{% if event %}) vanishes. The fix keeps a minimal global nav (Portfolio + People)
always visible so the menu never collapses to just the logo.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from app.main import create_app


def _client_db():
    d = tempfile.mkdtemp()
    os.environ["DB_PATH"] = os.path.join(d, "t.db")
    return TestClient(create_app())


def test_people_page_shows_global_nav_not_just_logo():
    client = _client_db()
    html = client.get("/people").text
    # Portfolio + People global links present even with no event context.
    assert 'href="/portfolio"' in html
    assert 'href="/people"' in html
    # A shell nav element is present (the menu did not collapse).
    assert 'shell-nav' in html


def test_home_shows_people_and_portfolio_global_links():
    client = _client_db()
    html = client.get("/").text
    assert 'href="/portfolio"' in html
    assert 'href="/people"' in html
