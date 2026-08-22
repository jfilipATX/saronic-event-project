"""Server-side Claude client — the SINGLE owner of ANTHROPIC_API_KEY.

Architecture gate (code-review #1 + #2):
  * This module is imported only by backend code. The frontend never sees the key.
  * Every Claude call goes through ``ClaudeClient.complete()``, which routes through
    the ``SpendMeter`` (halt *before* the call at the limit) and maps Anthropic
    failure modes to clean, typed ``ClaudeError`` subclasses.
  * The real implementation is selected by ``get_client(config)``: if
    ``config.claude_enabled`` is false (default mock mode), a deterministic
    ``MockClaudeClient`` is returned so the app runs fully offline and free.

The interface is intentionally tiny so features depend on a Protocol, not a vendor
SDK — keeping "where we leaned on Claude vs our own judgment" visible in code.
"""
from __future__ import annotations

import abc
import os
import time
from typing import Any, Mapping

from app.claude.errors import (
    BudgetExceededError,
    ClaudeError,
    EmptyResponseError,
    ExpiredKeyError,
    ModelUnavailableError,
    RateLimitError,
)
from app.claude.meter import SpendMeter
from app.config import Config


class ClaudeClient(abc.ABC):
    """Minimal completion interface every feature depends on."""

    @abc.abstractmethod
    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024,
                 temperature: float = 0.3) -> str:
        """Return assistant text for a single turn. Raise typed ClaudeError on failure."""
        raise NotImplementedError


class MockClaudeClient(ClaudeClient):
    """Deterministic offline stand-in.

    Returns a structured, clearly-marked mock so features are runnable and testable
    with zero external calls and zero spend. The shape mirrors a real response so
    swapping in ``RealClaudeClient`` requires no feature changes.
    """

    def __init__(self, meter: SpendMeter | None = None) -> None:
        self._meter = meter

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024,
                 temperature: float = 0.3) -> str:
        if self._meter is not None:
            self._meter.ensure_budget()
            # Mock "usage": cheap, but still exercises the meter path.
            self._meter.record(input_tokens=len(prompt) // 4,
                               output_tokens=24)
        return (
            "[MOCK CLAUDE] Offline completion. system="
            f"{system[:40]!r} prompt={prompt[:60]!r}"
        )


class RealClaudeClient(ClaudeClient):
    """Real Anthropic-backed client. Imports the SDK lazily (server-side only)."""

    def __init__(self, config: Config, meter: SpendMeter, ledger=None) -> None:
        if not config.anthropic_api_key:
            raise ExpiredKeyError()
        self._config = config
        self._meter = meter
        #: Optional persistent ledger (P3-1). Optional so the client stays
        #: usable without a database, but wired at this one gateway so no
        #: surface can spend without being recorded.
        self._ledger = ledger
        self._model = config.anthropic_model
        self._sdk = None  # lazily set on first use
        self._temp_ok: bool | None = None  # SDK capability, detected once

    def _get_sdk(self):
        if self._sdk is None:
            try:
                import anthropic  # type: ignore
            except Exception as exc:  # pragma: no cover - import env dependent
                raise ClaudeError(
                    "The anthropic SDK is not installed in this environment."
                ) from exc
            self._sdk = anthropic.Anthropic(api_key=self._config.anthropic_api_key)
        return self._sdk

    def _supports_temperature(self, sdk) -> bool:
        """Whether this SDK version accepts a top-level ``temperature``.

        The anthropic SDK moved ``temperature`` out of ``messages.create()``'s
        signature in 1.0; passing it raises TypeError there, while older versions
        require it to control determinism. Detect once rather than pinning a
        version, so the wrapper works across both.
        """
        if self._temp_ok is None:
            try:
                import inspect

                params = inspect.signature(sdk.messages.create).parameters
                self._temp_ok = "temperature" in params
            except (TypeError, ValueError):  # pragma: no cover - exotic SDKs
                self._temp_ok = False
        return self._temp_ok

    def _log(self, *, surface, event_id, usd=0.0, input_tokens=0,
             output_tokens=0, error=None) -> None:
        if self._ledger is None or not surface:
            return
        try:
            self._ledger.record(
                surface=surface, model=self._model, event_id=event_id,
                input_tokens=input_tokens, output_tokens=output_tokens,
                usd=usd, error=error)
        except Exception:  # noqa: BLE001 - bookkeeping must never break a call
            pass

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024,
                 temperature: float = 0.3, event_id=None, surface: str = "") -> str:
        # Guard spend BEFORE the network call.
        self._meter.ensure_budget()
        sdk = self._get_sdk()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._supports_temperature(sdk):
            kwargs["temperature"] = temperature
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = sdk.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - map vendor errors to ours
                err = self._map_error(exc)
                if err.retryable and attempt <= 3:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                # Nothing billed: the call never completed. Logged at $0 so the
                # ledger shows the attempt without inventing a cost.
                self._log(surface=surface, event_id=event_id, usd=0.0,
                          error=type(err).__name__)
                raise err
            # Record spend (exact if provided, else estimate).
            usage = getattr(resp, "usage", None)
            billed_in = getattr(usage, "input_tokens", 0) if usage else 0
            billed_out = getattr(usage, "output_tokens", 0) if usage else 0
            before = self._meter.spent
            if usage is not None:
                self._meter.record(input_tokens=billed_in, output_tokens=billed_out)
            call_usd = round(self._meter.spent - before, 6)
            text = "".join(
                block.text for block in resp.content
                if getattr(block, "type", "") == "text"
            )
            if not text.strip():
                # Reasoning models emit `thinking` blocks that draw from the same
                # max_tokens budget. If thinking exhausts it, the text block comes
                # back empty while the call still bills — silently returning "" here
                # would look like success and ship a blank slide. Fail loudly.
                stop = getattr(resp, "stop_reason", None)
                kinds = sorted({getattr(b, "type", "?") for b in resp.content})
                # This one BILLED and gave us nothing usable. Logging its real
                # cost is the point: an empty response is our bug, not a
                # discount, and hiding it would make the ledger disagree with
                # the invoice.
                self._log(surface=surface, event_id=event_id, usd=call_usd,
                          input_tokens=billed_in, output_tokens=billed_out,
                          error="EmptyResponseError")
                raise EmptyResponseError(stop_reason=stop, block_types=kinds)
            self._log(surface=surface, event_id=event_id, usd=call_usd,
                      input_tokens=billed_in, output_tokens=billed_out)
            return text

    def _map_error(self, exc: Exception) -> ClaudeError:
        etype = type(exc).__name__
        status = getattr(exc, "status_code", None)
        if status == 401:
            return ExpiredKeyError()
        if status == 429:
            retry_after = getattr(exc, "response", None)
            retry_after = getattr(retry_after, "headers", {}).get("retry-after") if retry_after else None
            return RateLimitError(retry_after=float(retry_after) if retry_after else None)
        if status == 404:
            # The client's own config, never the environment: a model set
            # programmatically would otherwise be reported as "?" on the one
            # path where knowing it matters.
            return ModelUnavailableError(self._config.anthropic_model)
        # Heuristic fallbacks.
        if "api_key" in str(exc).lower() or "authentication" in str(exc).lower():
            return ExpiredKeyError()
        return ClaudeError(f"Claude request failed: {etype}: {exc}")


def get_client(config: Config | None = None,
               meter: SpendMeter | None = None,
               ledger=None) -> ClaudeClient:
    """Factory: returns a Mock client unless real Claude is explicitly enabled.

    This is the ONE function features call — they never instantiate the real SDK
    directly, which keeps the key server-side and the meter mandatory.
    """
    cfg = config or Config()
    if meter is None:
        meter = SpendMeter(limit_usd=cfg.anthropic_spend_limit)
    if cfg.claude_enabled:
        return RealClaudeClient(cfg, meter, ledger=ledger)
    # Mock mode costs nothing and must never write to the ledger: a fabricated
    # cost would corrupt the one number this feature exists to report.
    return MockClaudeClient(meter)
