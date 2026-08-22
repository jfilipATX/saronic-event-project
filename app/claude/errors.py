"""Typed, user-facing error hierarchy for Claude calls.

These are what the rest of the app catches when something goes wrong with the
model — every error carries a *clean* message (no secret/key material, no stack
trace leakage). The README documents how we map Anthropic failure modes to these.
"""
from __future__ import annotations


class ClaudeError(Exception):
    """Base class for all Claude-client failures."""

    #: Whether the caller should retry (with backoff) the same request.
    retryable: bool = False


class ExpiredKeyError(ClaudeError):
    """The ANTHROPIC_API_KEY is expired/invalid (401 from the API)."""

    def __init__(self) -> None:
        super().__init__(
            "The Anthropic API key is expired or invalid. "
            "Please provide a current key and retry."
        )


class BudgetExceededError(ClaudeError):
    """Spend meter would exceed ANTHROPIC_SPEND_LIMIT on the next call."""

    def __init__(self, spent: float, limit: float) -> None:
        self.spent = spent
        self.limit = limit
        super().__init__(
            f"Claude spend limit reached (${spent:.2f} / ${limit:.2f}). "
            "Halting before the next call to protect the key budget."
        )


class RateLimitError(ClaudeError):
    """HTTP 429 — too many requests. Retryable with backoff."""

    retryable = True

    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        msg = "Claude rate limit hit (429). Backing off before retrying."
        if retry_after:
            msg += f" Suggested wait: {retry_after:.0f}s."
        super().__init__(msg)


class ModelUnavailableError(ClaudeError):
    """Model alias 404s or proxies to a non-Anthropic endpoint."""

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(
            f"Model '{model}' is unavailable or not served by Anthropic. "
            "Verify the alias resolves to a real Claude model."
        )


class EmptyResponseError(ClaudeError):
    """The call succeeded and billed, but produced no usable text.

    Real cause seen in practice: reasoning models (opus-5 and similar) emit
    ``thinking`` blocks that draw from the same ``max_tokens`` budget. When
    thinking exhausts the budget the response stops with ``max_tokens`` and the
    text block is empty — a silent blank that would otherwise be mistaken for a
    successful completion. Retrying with a larger budget usually resolves it.
    """

    retryable = True

    def __init__(self, stop_reason: str | None = None,
                 block_types: list[str] | None = None) -> None:
        self.stop_reason = stop_reason
        self.block_types = block_types or []
        detail = f"stop_reason={stop_reason!r}, blocks={self.block_types}"
        hint = ""
        if stop_reason == "max_tokens":
            hint = (" The token budget was consumed before any text was produced"
                    " — raise max_tokens.")
        super().__init__(
            f"Claude returned no text content ({detail})."+hint
        )
