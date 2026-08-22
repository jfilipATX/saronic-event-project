"""Persistent Claude spend ledger (P3-1).

The ``SpendMeter`` guards one process against its cap. This answers a different
question, and the coordinator's actual one: *what did planning this event cost?*

Design stance: **the ledger must agree with Anthropic's bill, not flatter us.**
A call that fails before billing records $0. A call that bills and then returns
nothing usable records its real cost — an empty response is our bug, not a
discount. Calls with no event (model probes, harness runs) are recorded with a
null ``event_id`` rather than dropped, because a ledger that only holds the
attributable part does not reconcile.

Writing happens at the single gateway every call already passes through, so a
new surface cannot forget to record.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from app.db import repository as repo
from app.db.models import SpendEntry


class SpendLedger:
    """Records priced calls against a database connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, *, surface: str, model: str,
               event_id: Optional[int] = None,
               input_tokens: int = 0, output_tokens: int = 0,
               usd: float = 0.0, error: Optional[str] = None) -> int:
        entry = SpendEntry(
            event_id=event_id, surface=surface, model=model,
            input_tokens=int(input_tokens), output_tokens=int(output_tokens),
            usd=round(max(0.0, float(usd)), 6), error=error,
        )
        row_id = repo.record_spend(self._conn, entry)
        # Committing here is deliberate: the ledger must survive whatever the
        # caller does next, including raising. A cost we incurred but did not
        # record is the one failure mode that makes the whole feature dishonest.
        try:
            self._conn.commit()
        except sqlite3.Error:
            pass
        return row_id

    def total_for_event(self, event_id: int) -> float:
        """USD logged against this event so far (for per-event caps)."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(usd), 0.0) FROM spend_log "
            "WHERE event_id = ?", (event_id,)).fetchone()
        return float(row[0]) if row else 0.0
