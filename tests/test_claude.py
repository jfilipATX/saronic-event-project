"""Claude wrapper + SpendMeter — the budget guard and failure mapping.

This is the layer that stands between the project and a $300 key, so the tests
are about the guard rails, not the happy path: does spend halt *before* the
network call, do vendor errors become typed errors, and does the mock path stay
genuinely free.

No network here — the SDK is stubbed. The live pass is a separate script.
"""
from __future__ import annotations

import pytest

from app.claude.client import (
    MockClaudeClient,
    RealClaudeClient,
    get_client,
)
from app.claude.errors import (
    EmptyResponseError,
    BudgetExceededError,
    ClaudeError,
    ExpiredKeyError,
    ModelUnavailableError,
    RateLimitError,
)
from app.claude.meter import SpendMeter
from app.config import Config


class TestSpendMeter:
    def test_starts_empty_and_reports_remaining(self):
        m = SpendMeter(limit_usd=10.0)
        assert m.spent == 0.0
        assert m.remaining == 10.0

    def test_rejects_a_nonpositive_limit(self):
        with pytest.raises(ValueError):
            SpendMeter(limit_usd=0)

    def test_record_accumulates_token_based_spend(self):
        m = SpendMeter(limit_usd=10.0)
        m.record(input_tokens=1000, output_tokens=0)
        assert m.spent > 0

    def test_exact_cost_overrides_the_token_estimate(self):
        m = SpendMeter(limit_usd=10.0)
        m.record(input_tokens=999_999, exact_usd=0.25)
        assert m.spent == 0.25

    def test_ensure_budget_passes_below_the_limit(self):
        SpendMeter(limit_usd=10.0).ensure_budget()

    def test_ensure_budget_raises_before_breaching(self):
        m = SpendMeter(limit_usd=1.0, spent_usd=0.99)
        with pytest.raises(BudgetExceededError):
            m.ensure_budget(estimated_usd=0.5)

    def test_budget_error_reports_spent_and_limit(self):
        m = SpendMeter(limit_usd=1.0, spent_usd=1.0)
        with pytest.raises(BudgetExceededError) as exc:
            m.ensure_budget(estimated_usd=0.5)
        assert "1.0" in str(exc.value)

    def test_remaining_never_goes_negative(self):
        m = SpendMeter(limit_usd=1.0)
        m.record(exact_usd=5.0)
        assert m.remaining == 0.0


class TestMockClient:
    def test_mock_returns_a_clearly_marked_response(self):
        out = MockClaudeClient().complete(system="s", prompt="p")
        assert "MOCK" in out

    def test_mock_still_exercises_the_meter(self):
        m = SpendMeter(limit_usd=10.0)
        MockClaudeClient(m).complete(system="s", prompt="p" * 400)
        assert m.spent > 0

    def test_mock_halts_when_the_budget_is_gone(self):
        m = SpendMeter(limit_usd=0.01, spent_usd=0.01)
        with pytest.raises(BudgetExceededError):
            MockClaudeClient(m).complete(system="s", prompt="p")


class _StubMessages:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self._behaviour(self.calls, kwargs)


class _StubSDK:
    def __init__(self, behaviour):
        self.messages = _StubMessages(behaviour)


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _Resp:
    def __init__(self, text, usage=None):
        self.content = [_Block(text)]
        self.usage = usage


def _real(meter, behaviour, key="sk-test"):
    cfg = Config(anthropic_api_key=key, use_real_claude=True)
    client = RealClaudeClient(cfg, meter)
    client._sdk = _StubSDK(behaviour)
    return client


class TestRealClientBudgetGuard:
    def test_budget_is_checked_before_any_network_call(self):
        """The guard must halt without ever reaching the SDK."""
        m = SpendMeter(limit_usd=0.01, spent_usd=0.01)

        def must_not_run(calls, kwargs):
            raise AssertionError("SDK called despite exhausted budget")

        client = _real(m, must_not_run)
        with pytest.raises(BudgetExceededError):
            client.complete(system="s", prompt="p")
        assert client._sdk.messages.calls == 0

    def test_missing_key_is_rejected_at_construction(self):
        with pytest.raises(ExpiredKeyError):
            RealClaudeClient(Config(anthropic_api_key=""), SpendMeter(limit_usd=1))

    def test_successful_call_records_reported_usage(self):
        m = SpendMeter(limit_usd=10.0)
        client = _real(m, lambda c, k: _Resp("hello", _Usage(1000, 500)))
        assert client.complete(system="s", prompt="p") == "hello"
        assert m.spent > 0

    def test_response_text_blocks_are_concatenated(self):
        m = SpendMeter(limit_usd=10.0)
        client = _real(m, lambda c, k: _Resp("answer", _Usage(10, 10)))
        assert client.complete(system="s", prompt="p") == "answer"

    def test_model_and_prompt_reach_the_sdk(self):
        m = SpendMeter(limit_usd=10.0)
        seen = {}

        def capture(calls, kwargs):
            seen.update(kwargs)
            return _Resp("ok", _Usage(1, 1))

        _real(m, capture).complete(system="SYS", prompt="PROMPT", max_tokens=77)
        assert seen["system"] == "SYS"
        assert seen["messages"][0]["content"] == "PROMPT"
        assert seen["max_tokens"] == 77


class TestRealClientErrorMapping:
    def _err(self, status):
        class _E(Exception):
            status_code = status

        return _E("boom")

    def test_401_becomes_expired_key(self):
        m = SpendMeter(limit_usd=10.0)

        def boom(c, k):
            raise self._err(401)

        with pytest.raises(ExpiredKeyError):
            _real(m, boom).complete(system="s", prompt="p")

    def test_404_becomes_model_unavailable(self):
        m = SpendMeter(limit_usd=10.0)

        def boom(c, k):
            raise self._err(404)

        with pytest.raises(ModelUnavailableError):
            _real(m, boom).complete(system="s", prompt="p")

    def test_unknown_failure_becomes_a_generic_claude_error(self):
        m = SpendMeter(limit_usd=10.0)

        def boom(c, k):
            raise RuntimeError("something odd")

        with pytest.raises(ClaudeError):
            _real(m, boom).complete(system="s", prompt="p")

    def test_a_failed_call_records_no_spend(self):
        """We must not bill ourselves for calls that never produced output."""
        m = SpendMeter(limit_usd=10.0)

        def boom(c, k):
            raise self._err(401)

        with pytest.raises(ExpiredKeyError):
            _real(m, boom).complete(system="s", prompt="p")
        assert m.spent == 0.0


class TestGetClientFactory:
    def test_defaults_to_the_mock_client(self):
        assert isinstance(get_client(Config()), MockClaudeClient)

    def test_mock_is_used_even_when_a_key_is_present_but_not_enabled(self):
        """USE_REAL_CLAUDE is an explicit opt-in; a key alone must not spend."""
        cfg = Config(anthropic_api_key="sk-test", use_real_claude=False)
        assert isinstance(get_client(cfg), MockClaudeClient)

    @pytest.mark.real_claude_config
    def test_real_client_requires_both_the_flag_and_the_key(self):
        cfg = Config(anthropic_api_key="sk-test", use_real_claude=True)
        assert isinstance(get_client(cfg), RealClaudeClient)

    def test_enabled_flag_without_a_key_stays_on_mock(self):
        cfg = Config(anthropic_api_key="", use_real_claude=True)
        assert isinstance(get_client(cfg), MockClaudeClient)

    def test_factory_supplies_a_meter_bound_to_the_configured_limit(self):
        cfg = Config(anthropic_api_key="sk-test", use_real_claude=True,
                     anthropic_spend_limit=42.0)
        client = get_client(cfg)
        assert client._meter.limit == 42.0


class TestEmptyResponseIsNotSilentSuccess:
    """Reasoning models can burn max_tokens on thinking and return no text.

    Found in the real pass: a surface reported 'ok' with 0 characters while
    still billing. A blank that looks like success is worse than a loud failure.
    """

    def test_text_only_response_with_no_text_raises(self):
        m = SpendMeter(limit_usd=10.0)

        class _Thinking:
            type = "thinking"
            text = ""

        class _EmptyText:
            type = "text"
            text = ""

        class _R:
            content = [_Thinking(), _EmptyText()]
            usage = _Usage(80, 700)
            stop_reason = "max_tokens"

        client = _real(m, lambda c, k: _R())
        with pytest.raises(EmptyResponseError) as exc:
            client.complete(system="s", prompt="p")
        assert "max_tokens" in str(exc.value)

    def test_empty_response_still_records_the_spend_it_incurred(self):
        """The call billed even though it produced nothing; the meter must know."""
        m = SpendMeter(limit_usd=10.0)

        class _R:
            content = []
            usage = _Usage(80, 700)
            stop_reason = "max_tokens"

        with pytest.raises(EmptyResponseError):
            _real(m, lambda c, k: _R()).complete(system="s", prompt="p")
        assert m.spent > 0

    def test_thinking_plus_real_text_returns_only_the_text(self):
        m = SpendMeter(limit_usd=10.0)

        class _Thinking:
            type = "thinking"
            text = "internal reasoning"

        class _R:
            content = [_Thinking(), _Block("the actual answer")]
            usage = _Usage(80, 200)
            stop_reason = "end_turn"

        out = _real(m, lambda c, k: _R()).complete(system="s", prompt="p")
        assert out == "the actual answer"


@pytest.mark.real_claude_config
class TestConfigRequiresAKeyNotJustAFlag:
    """USE_REAL_CLAUDE=1 with an empty key must not enable real Claude.

    Found when the suite jumped from 3s to 27s: every slides test was making a
    real network call that failed slowly. A flag without a key is a
    misconfiguration, and treating it as 'enabled' turns an offline test run into
    a network-dependent one.
    """

    def test_flag_without_a_key_is_not_enabled(self, monkeypatch):
        from app.config import load_config

        monkeypatch.setenv("USE_REAL_CLAUDE", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        assert load_config().claude_enabled is False

    def test_flag_with_a_whitespace_key_is_not_enabled(self, monkeypatch):
        from app.config import load_config

        monkeypatch.setenv("USE_REAL_CLAUDE", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        assert load_config().claude_enabled is False

    def test_flag_with_a_real_key_is_enabled(self, monkeypatch):
        from app.config import load_config

        monkeypatch.setenv("USE_REAL_CLAUDE", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 90)
        assert load_config().claude_enabled is True

    def test_key_without_the_flag_stays_disabled(self, monkeypatch):
        from app.config import load_config

        monkeypatch.setenv("USE_REAL_CLAUDE", "0")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 90)
        assert load_config().claude_enabled is False
