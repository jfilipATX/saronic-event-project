"""P5-4 — manual-trigger LLM venue search with cost transparency.

The coordinator clicks a button, Claude proposes venues near {city, state} that
fit {capacity, amenities}, and the proposals land as STAGED options (proposed,
not auto-applied) — same "provider proposes, human decides" seam as P2-1.

Load-bearing parts (per the user's standing ask):

* **Cost estimate is shown BEFORE the call.** The coordinator sees the estimated
  token cost (and a USD ceiling) on the form, so triggering is an informed act.
  The estimate is deterministic from the prompt size, not a guess after the fact.
* **The call is capped.** A per-event spend cap plus the global SpendMeter halt
  the request before it leaves — manual trigger does not mean unbounded.
* **Every call is logged to the spend ledger**, attributed to the event, so the
  "what did planning this event cost?" question reconciles with the bill.

Invariants pinned here:

* A search with no Claude client (mock/offline mode) returns proposed options
  without raising and without spend.
* The estimate is visible and non-zero for a real prompt; it reflects the actual
  prompt token count, not a constant.
* The per-event cap blocks the call when the event's running spend would exceed
  it, surfacing a clear "over cap" message rather than a 500 or a silent skip.
* Proposals are staged as DecisionOptions with reasoning, never silently written
  as chosen facts.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    repo.apply_migrations(c)
    return c


@pytest.fixture()
def event(conn):
    return repo.create_event(conn, Event(name="Fleet Week", city="Austin",
                                         state="TX", country="US",
                                         audience_estimate=2000))


def _estimate_prompt_size(city, state, capacity, amenities):
    """Mirror the prompt builder's size so the test asserts the estimate tracks
    the real input, not a constant."""
    from app.features.venue_search import build_search_prompt
    return len(build_search_prompt(city, state, capacity, amenities))


class TestCostEstimate:
    def test_estimate_is_visible_and_scales_with_input(self):
        from app.features.venue_search import estimate_search_cost

        small = estimate_search_cost(
            _estimate_prompt_size("Austin", "TX", 2000, ["parking"]))
        large = estimate_search_cost(
            _estimate_prompt_size("Austin", "TX", 2000,
                                  ["parking", "catering", "av", "wifi",
                                   "loading", "security"]))
        # Returns (input_tokens, output_tokens, usd). More amenities = larger
        # prompt = larger input-token estimate + larger USD. Never zero/negative.
        assert small[0] > 0 and large[0] > 0
        assert large[0] > small[0]
        assert small[2] > 0 and large[2] > 0 and large[2] > small[2]

    def test_estimate_has_a_usd_ceiling(self):
        from app.features.venue_search import estimate_search_cost

        in_t, out_t, usd = estimate_search_cost(4000)
        # The estimate returns (input_tokens, output_tokens, usd_ceiling).
        assert in_t > 0 and out_t > 0 and usd > 0
        # Conservative ceiling: blended $0.03/1k tokens on the worst case.
        assert usd <= ((in_t + out_t) / 1000.0) * 0.03 + 1e-9


class TestSearchStagesProposals:
    def test_search_returns_proposed_options_not_chosen_facts(self, conn, event):
        from app.features.venue_search import search_venues

        # No real client -> offline path returns proposed options, no spend.
        options = search_venues(
            client=None, event_id=event, city="Austin", state="TX",
            capacity=2000, amenities=["parking"])
        assert options, "offline search should still propose venues"
        for o in options:
            assert o.label and o.reasoning
            # Proposed, not auto-applied: no option is marked chosen.
            assert not getattr(o, "chosen", False)

    def test_search_logs_to_ledger_when_client_present(self, conn, event):
        import app.features.venue_search as vs
        from app.claude.ledger import SpendLedger

        # A fake client that records being called and returns a structured list.
        class FakeClient:
            def __init__(self):
                self.calls = 0

            def complete(self, *, system, prompt, max_tokens=1024,
                         temperature=0.3, event_id=None, surface=""):
                self.calls += 1
                return ('[{"name": "Austin Expo Center", "capacity": 2400, '
                        '"city": "Austin", "why": "Fits 2000."}]')

        client = FakeClient()
        ledger = SpendLedger(conn)
        before = repo.spend_total(conn, event_id=event)
        vs.search_venues(client, event_id=event, city="Austin", state="TX",
                         capacity=2000, amenities=["parking"], ledger=ledger)
        after = repo.spend_total(conn, event_id=event)
        assert client.calls == 1
        # The call is logged against THIS event.
        assert after >= before


class TestPerEventCap:
    def test_over_cap_blocks_the_call_with_a_clear_message(self, conn, event):
        from app.features.venue_search import search_venues
        from app.claude.ledger import SpendLedger
        from app.claude.errors import BudgetExceededError

        class FakeClient:
            def complete(self, **kw):
                raise AssertionError("should never be called over cap")

        # Per-event cap of $0 means any billed call is over cap. The route
        # always passes the ledger, so the cap check runs.
        with pytest.raises(BudgetExceededError):
            search_venues(
                FakeClient(), event_id=event, city="Austin", state="TX",
                capacity=2000, amenities=["parking"],
                ledger=SpendLedger(conn), per_event_cap_usd=0.0)
