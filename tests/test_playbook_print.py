"""Playbook print-view readability + PII-scope guards.

These pin the "day-of reference must read on paper" contract so the
P6-5b readability regression (web-page-density forced to A4) can't silently
return. They assert the *rendered* CSS contracts, not implementation.
"""

import pytest
from app.db import repository as repo
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app(db_path=str(tmp_path / "qa.db"))
    with TestClient(app) as c:
        yield c


def _seed(client, tmp_path):
    db = str(tmp_path / "qa.db")
    import sqlite3
    from app.db import schema_sql_text as sql
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(sql.SCHEMA)
    repo.apply_migrations(con)
    eid = repo.create_event(
        con, repo.Event(name="Fleet Week", city="San Diego", country="US",
                        state="CA"))
    repo.record_decision(con, repo.Decision(
        event_id=eid, step="venue", question="Which venue?",
        chosen_key="k", options=[repo.DecisionOption(
            key="k", label="Port Alpha", reasoning="Fits 5,000 cap.")]))
    pid = repo.add_person(con, repo.Person(name="Sam", role="Ops"))
    pid2 = repo.add_person(con, repo.Person(name="Dana", role="Protocol"))
    repo.assign_staff(con, eid, pid, role="Ops", can_check_in=True)
    repo.assign_staff(con, eid, pid2, role="Protocol", can_check_in=False)
    con.execute(
        "INSERT INTO attendees (event_id, full_name, email, company, is_vip) "
        "VALUES (?,?,?,?,?)", (eid, "Jane President", "jane@gov.example",
                               "State Dept", 1))
    con.execute(
        "INSERT INTO segments (event_id,title,start,end,track,kind,owners_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (eid, "Panel", "2026-09-04 15:30", "2026-09-04 16:30", "program",
         "panel", str([pid, pid2])))
    con.commit()
    con.close()
    return eid


def test_print_renders_no_chrome(client, tmp_path):
    eid = _seed(client, tmp_path)
    r = client.get(f"/events/{eid}/playbook/print")
    assert r.status_code == 200
    body = r.text
    # Standalone doc: no nav element or stepper markup (the word "stepper"
    # also appears in a code comment, so match actual tags, not substrings).
    assert "<nav" not in body
    assert "class=\"stepper\"" not in body
    assert "<button" not in body and "btn-primary" not in body


def test_print_excludes_attendee_email(client, tmp_path):
    eid = _seed(client, tmp_path)
    r = client.get(f"/events/{eid}/playbook/print")
    # VIP shows name/company only — contact is PII, never printed.
    assert "jane@gov.example" not in r.text
    assert "Jane President" in r.text
    assert "State Dept" in r.text


def test_print_amber_flag_present(client, tmp_path):
    eid = _seed(client, tmp_path)
    r = client.get(f"/events/{eid}/playbook/print")
    # Double-booked owner flag survives (print-color-adjust: exact).
    assert "print-ros-conflict" in r.text


def test_print_readability_contract(client, tmp_path):
    eid = _seed(client, tmp_path)
    r = client.get(f"/events/{eid}/playbook/print")
    css = r.text
    # Body font at least 12pt (paper-readable, not web-density).
    assert "font-size: 12" in css or "font-size: 12.5pt" in css
    # Muted must not be the old low-contrast grey that failed on paper.
    assert "#55636E" not in css and "#9DA7AF" not in css
    # Page constrained so content doesn't clip at the edge.
    assert "max-width" in css
