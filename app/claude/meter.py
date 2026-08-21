"""Spend meter — the structural guard against burning the $300 key cap.

Design contract (per code-review gate #2):
  * Tracks cumulative estimated spend toward ``ANTHROPIC_SPEND_LIMIT``.
  * ``ensure_budget()`` is called *before* each API request and raises
    ``BudgetExceededError`` BEFORE the call is made (never mid-request).
  * ``record()`` adds an estimate after a response. When the upstream API does
    not return an exact cost we fall back to a conservative per-token estimate.

The meter is intentionally pure-Python and side-effect free except for the
in-memory ``_spent`` counter, so it is trivially unit-testable offline.
"""
from __future__ import annotations

from app.claude.errors import BudgetExceededError

# Conservative fallback price (USD per 1k tokens, blended input+output) used when
# the API does not return an exact cost. Opus-class is the expensive end; using it
# keeps the guard conservative (halts earlier rather than later).
_FALLBACK_USD_PER_1K_TOKENS = 0.03


class SpendMeter:
    def __init__(self, limit_usd: float, spent_usd: float = 0.0) -> None:
        if limit_usd <= 0:
            raise ValueError("spend limit must be positive")
        self._limit = self.__class__._round(limit_usd)
        self._spent = self.__class__._round(spent_usd)

    @staticmethod
    def _round(x: float) -> float:
        return round(x, 4)

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def limit(self) -> float:
        return self._limit

    @property
    def remaining(self) -> float:
        return self._round(max(0.0, self._limit - self._spent))

    def ensure_budget(self, estimated_usd: float | None = None) -> None:
        """Raise ``BudgetExceededError`` if the next call would breach the limit.

        ``estimated_usd`` lets callers pass a worst-case estimate for the upcoming
        request; when omitted we assume the fallback cost of one blended call.
        """
        if estimated_usd is None:
            estimated_usd = _FALLBACK_USD_PER_1K_TOKENS
        projected = self._round(self._spent + max(0.0, estimated_usd))
        if projected > self._limit:
            raise BudgetExceededError(spent=self._spent, limit=self._limit)

    def record(self, *, input_tokens: int = 0, output_tokens: int = 0,
               exact_usd: float | None = None) -> float:
        """Record spend for a completed call; returns the new total."""
        if exact_usd is not None:
            added = max(0.0, float(exact_usd))
        else:
            tokens = input_tokens + output_tokens
            added = (tokens / 1000.0) * _FALLBACK_USD_PER_1K_TOKENS
        self._spent = self._round(self._spent + added)
        return self._spent
