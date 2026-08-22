"""P3-1 — persistent, per-event Claude spend ledger.

The question this answers is the coordinator's: *what did planning this event
cost?* The SpendMeter guards a single process against its cap; it is not a
ledger, so the number vanishes when the process exits.

Two rules that decide whether the ledger is trustworthy:

* **It must agree with Anthropic's bill, not flatter us.** A call that fails
  before billing logs $0; a call that bills and then returns nothing usable logs
  its real cost. An empty response is our problem, not a discount.
* **No call may bypass it.** It is written at the same single gateway the meter
  uses, so a new surface cannot forget to record.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.claude.errors import EmptyResponseError, ExpiredKeyError
from app.claude.ledger import SpendLedger
from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event, SpendEntry


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def event(conn) -> int:
    return repo.create_event(conn, Event(name="Fleet Week", city="Austin"))


class TestRecording:
    def test_an_entry_is_persisted(self, conn, event):
        SpendLedger(conn).record(event_id=event, surface="slide_copy",
                                 model="claude-opus-5", input_tokens=800,
                                 output_tokens=120, usd=0.0182)
        entries = repo.spend_entries(conn)
        assert len(entries) == 1
        assert entries[0].surface == "slide_copy"
        assert entries[0].usd == pytest.approx(0.0182)

    def test_tokens_and_model_are_kept(self, conn, event):
        SpendLedger(conn).record(event_id=event, surface="url_extract",
                                 model="claude-opus-5", input_tokens=4200,
                                 output_tokens=310, usd=0.0731)
        entry = repo.spend_entries(conn)[0]
        assert (entry.input_tokens, entry.output_tokens) == (4200, 310)
        assert entry.model == "claude-opus-5"

    def test_every_entry_is_timestamped(self, conn, event):
        SpendLedger(conn).record(event_id=event, surface="s", model="m", usd=0.01)
        assert repo.spend_entries(conn)[0].created_at

    def test_an_unattributed_call_logs_with_a_null_event(self, conn):
        """Probes and harness runs still belong in the ledger."""
        SpendLedger(conn).record(event_id=None, surface="model_probe",
                                 model="claude-opus-5", usd=0.0)
        entry = repo.spend_entries(conn)[0]
        assert entry.event_id is None

    def test_a_failed_call_logs_zero(self, conn, event):
        SpendLedger(conn).record(event_id=event, surface="slide_copy",
                                 model="claude-opus-5", usd=0.0, error="ExpiredKeyError")
        entry = repo.spend_entries(conn)[0]
        assert entry.usd == 0.0
        assert entry.error == "ExpiredKeyError"

    def test_a_billed_but_empty_call_logs_its_real_cost(self, conn, event):
        """An empty response is our bug, not a discount — the bill still comes."""
        SpendLedger(conn).record(event_id=event, surface="audience_reasoning",
                                 model="claude-opus-5", input_tokens=900,
                                 output_tokens=2000, usd=0.0271,
                                 error="EmptyResponseError")
        entry = repo.spend_entries(conn)[0]
        assert entry.usd == pytest.approx(0.0271)
        assert entry.error == "EmptyResponseError"


class TestTotals:
    def _seed(self, conn, event, other):
        ledger = SpendLedger(conn)
        ledger.record(event_id=event, surface="slide_copy", model="m", usd=0.0182)
        ledger.record(event_id=event, surface="url_extract", model="m", usd=0.0731)
        ledger.record(event_id=other, surface="slide_copy", model="m", usd=0.0100)
        ledger.record(event_id=None, surface="model_probe", model="m", usd=0.0042)

    def test_per_event_total_counts_only_that_event(self, conn, event):
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        self._seed(conn, event, other)
        assert repo.spend_total(conn, event_id=event) == pytest.approx(0.0913)

    def test_global_total_includes_unattributed_calls(self, conn, event):
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        self._seed(conn, event, other)
        assert repo.spend_total(conn) == pytest.approx(0.1055)

    def test_an_event_with_no_calls_totals_zero(self, conn, event):
        assert repo.spend_total(conn, event_id=event) == 0.0

    def test_per_surface_breakdown_for_an_event(self, conn, event):
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        self._seed(conn, event, other)
        by_surface = repo.spend_by_surface(conn, event_id=event)
        assert by_surface["slide_copy"] == pytest.approx(0.0182)
        assert by_surface["url_extract"] == pytest.approx(0.0731)

    def test_entries_are_newest_first(self, conn, event):
        ledger = SpendLedger(conn)
        ledger.record(event_id=event, surface="first", model="m", usd=0.01)
        ledger.record(event_id=event, surface="second", model="m", usd=0.01)
        assert [e.surface for e in repo.spend_entries(conn)] == ["second", "first"]


class TestTheClientWritesToTheLedger:
    """No surface may bypass the ledger — it hangs off the one gateway."""

    class _Usage:
        input_tokens = 800
        output_tokens = 120

    class _Block:
        type = "text"
        text = "A headline."

    class _Resp:
        usage = None
        content = None
        stop_reason = "end_turn"

    def _sdk(self, response):
        class _Messages:
            def create(self, **kwargs):
                return response

        class _Sdk:
            messages = _Messages()

        return _Sdk()

    def _client(self, conn, sdk):
        from app.claude.client import RealClaudeClient
        from app.claude.meter import SpendMeter
        from app.config import Config

        cfg = Config(anthropic_api_key="sk-ant-x", anthropic_model="claude-opus-5",
                     use_real_claude=True)
        client = RealClaudeClient(cfg, SpendMeter(limit_usd=5.0),
                                  ledger=SpendLedger(conn))
        client._sdk = sdk
        client._temp_ok = False
        return client

    def test_a_successful_call_is_logged(self, conn, event):
        resp = self._Resp()
        resp.usage = self._Usage()
        resp.content = [self._Block()]
        client = self._client(conn, self._sdk(resp))
        client.complete(system="s", prompt="p", event_id=event, surface="slide_copy")
        entries = repo.spend_entries(conn)
        assert len(entries) == 1
        assert entries[0].event_id == event
        assert entries[0].surface == "slide_copy"
        assert entries[0].usd > 0

    def test_an_empty_response_is_logged_with_its_cost(self, conn, event):
        resp = self._Resp()
        resp.usage = self._Usage()
        resp.content = []
        client = self._client(conn, self._sdk(resp))
        with pytest.raises(EmptyResponseError):
            client.complete(system="s", prompt="p", event_id=event, surface="audience")
        entry = repo.spend_entries(conn)[0]
        assert entry.usd > 0
        assert entry.error == "EmptyResponseError"

    def test_a_call_that_never_billed_logs_zero(self, conn, event):
        class _Failing:
            class messages:
                @staticmethod
                def create(**kwargs):
                    exc = Exception("bad key")
                    exc.status_code = 401
                    raise exc

        client = self._client(conn, _Failing())
        with pytest.raises(ExpiredKeyError):
            client.complete(system="s", prompt="p", event_id=event, surface="slide_copy")
        entry = repo.spend_entries(conn)[0]
        assert entry.usd == 0.0
        assert entry.error

    def test_a_client_without_a_ledger_still_works(self, conn, event):
        """The ledger is optional plumbing, never a hard dependency."""
        from app.claude.client import RealClaudeClient
        from app.claude.meter import SpendMeter
        from app.config import Config

        resp = self._Resp()
        resp.usage = self._Usage()
        resp.content = [self._Block()]
        cfg = Config(anthropic_api_key="sk-ant-x", anthropic_model="claude-opus-5",
                     use_real_claude=True)
        client = RealClaudeClient(cfg, SpendMeter(limit_usd=5.0))
        client._sdk = self._sdk(resp)
        client._temp_ok = False
        assert client.complete(system="s", prompt="p") == "A headline."

    def test_the_mock_client_never_writes_to_the_ledger(self, conn, event):
        """Mock mode costs nothing; logging a fake cost would corrupt the answer."""
        from app.claude.client import MockClaudeClient

        MockClaudeClient().complete(system="s", prompt="p")
        assert repo.spend_entries(conn) == []


class TestLegacyDatabasesMigrate:
    def test_the_spend_log_table_is_added(self, tmp_path):
        path = str(tmp_path / "old.db")
        c = sqlite3.connect(path)
        c.executescript(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, city TEXT, created_at TEXT, "
            "audience_estimate INTEGER, event_type TEXT);")
        c.commit()
        c.close()
        repo.init_db(path)
        c = sqlite3.connect(path)
        try:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()
        assert "spend_log" in tables


class TestSpendInTheUi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    @pytest.fixture()
    def eid(self, client) -> int:
        r = client.post("/events", data={"name": "Fleet Week", "city": "Austin"},
                        follow_redirects=False)
        return int(r.headers["location"].rstrip("/").split("/")[2])

    def _log(self, event_id, **kw):
        import sqlite3

        import app.main as main_mod

        conn = sqlite3.connect(main_mod.CURRENT_DB)
        conn.row_factory = sqlite3.Row
        try:
            SpendLedger(conn).record(event_id=event_id, **kw)
        finally:
            conn.close()

    def test_the_playbook_states_zero_rather_than_hiding_it(self, client, eid):
        page = client.get(f"/events/{eid}/playbook").text
        assert "Claude API spend" in page
        assert "none (ran offline)" in page

    def test_the_playbook_shows_this_events_spend(self, client, eid):
        self._log(eid, surface="slide_copy", model="claude-opus-5", usd=0.0182)
        assert "0.0182" in client.get(f"/events/{eid}/playbook").text

    def test_the_playbook_excludes_another_events_spend(self, client, eid):
        r = client.post("/events", data={"name": "Other", "city": "Austin"},
                        follow_redirects=False)
        other = int(r.headers["location"].rstrip("/").split("/")[2])
        self._log(other, surface="slide_copy", model="m", usd=0.9900)
        assert "0.9900" not in client.get(f"/events/{eid}/playbook").text

    def test_the_usage_page_lists_entries_and_a_total(self, client, eid):
        self._log(eid, surface="slide_copy", model="claude-opus-5",
                  input_tokens=800, output_tokens=120, usd=0.0182)
        page = client.get("/usage").text
        assert "Claude API spend: $0.0182" in page
        assert "slide_copy" in page
        assert "Fleet Week" in page

    def test_unattributed_calls_are_visible_not_omitted(self, client, eid):
        self._log(None, surface="model_probe", model="claude-opus-5", usd=0.0042)
        page = client.get("/usage").text
        assert "Not event-specific" in page
        assert "model_probe" in page

    def test_the_global_total_includes_unattributed_calls(self, client, eid):
        self._log(eid, surface="slide_copy", model="m", usd=0.0100)
        self._log(None, surface="model_probe", model="m", usd=0.0042)
        assert "Claude API spend: $0.0142" in client.get("/usage").text

    def test_a_failed_call_is_shown_with_its_reason(self, client, eid):
        self._log(eid, surface="slide_copy", model="m", usd=0.0,
                  error="ExpiredKeyError")
        page = client.get("/usage").text
        assert "ExpiredKeyError" in page
        assert "$0.0000" in page

    def test_the_usage_page_is_reachable_from_home(self, client):
        assert "/usage" in client.get("/").text

    def test_an_empty_ledger_says_so(self, client):
        assert "entirely offline" in client.get("/usage").text
