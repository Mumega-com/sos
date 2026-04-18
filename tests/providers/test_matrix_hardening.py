"""Tests for v0.7.0 matrix hardening primitives.

Covers:
- call_with_breaker: success closes, failure records, open short-circuits
- probe_provider: 2xx records success, 5xx records failure, no URL no-ops
- select_with_fallback: walks tier order, skips open breakers, yields none on empty
"""
from __future__ import annotations

import pytest

from sos.providers.matrix import (
    CircuitBreakerConfig,
    ProviderCard,
    ProviderMatrixError,
    call_with_breaker,
    get_breaker,
    probe_provider,
    reset_breakers,
    select_with_fallback,
)


def make_card(**kwargs) -> ProviderCard:
    data = {
        "id": "test-provider",
        "name": "Test",
        "backend": "claude-adapter",
        "tier": "primary",
        "model": "claude-sonnet-4-6",
    }
    data.update(kwargs)
    return ProviderCard.model_validate(data)


@pytest.fixture(autouse=True)
def _clean_breakers():
    reset_breakers()
    yield
    reset_breakers()


class TestCallWithBreaker:
    async def test_success_resets_failures(self):
        card = make_card()
        breaker = get_breaker(card)
        breaker.failures = 2  # pretend we had prior failures

        async with call_with_breaker(card):
            pass

        assert breaker.failures == 0
        assert breaker.state == "closed"

    async def test_exception_records_failure_and_reraises(self):
        card = make_card(
            id="flaky",
            circuit_breaker=CircuitBreakerConfig(failure_threshold=2),
        )

        with pytest.raises(RuntimeError, match="boom"):
            async with call_with_breaker(card):
                raise RuntimeError("boom")

        breaker = get_breaker(card)
        assert breaker.failures == 1
        assert breaker.state == "closed"  # still below threshold

    async def test_opens_after_threshold(self):
        card = make_card(
            id="hostile",
            circuit_breaker=CircuitBreakerConfig(failure_threshold=2),
        )

        for _ in range(2):
            with pytest.raises(RuntimeError):
                async with call_with_breaker(card):
                    raise RuntimeError("still boom")

        breaker = get_breaker(card)
        assert breaker.state == "open"

    async def test_open_breaker_short_circuits(self):
        card = make_card(
            id="down",
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1),
        )

        with pytest.raises(RuntimeError):
            async with call_with_breaker(card):
                raise RuntimeError("first failure opens it")

        # Now breaker is open — next call should reject at entry.
        with pytest.raises(ProviderMatrixError, match="open"):
            async with call_with_breaker(card):
                pytest.fail("body should not execute when breaker is open")


class TestProbeProvider:
    async def test_no_health_probe_url_returns_false_without_recording(self):
        card = make_card(id="no-probe")  # health_probe_url is None
        breaker = get_breaker(card)
        initial_failures = breaker.failures

        ok = await probe_provider(card)

        assert ok is False
        assert breaker.failures == initial_failures  # untouched

    async def test_2xx_response_records_success(self, monkeypatch):
        card = make_card(
            id="healthy",
            health_probe_url="https://example.invalid/health",
        )

        class _Resp:
            status_code = 200

        class _Client:
            def __init__(self, *a, **kw): ...
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): return _Resp()

        import sos.providers.matrix as m

        class _HttpxStub:
            AsyncClient = _Client

        monkeypatch.setattr(
            "builtins.__import__",
            _patched_import({"httpx": _HttpxStub}, original=__builtins__["__import__"]),
        )

        breaker = get_breaker(card)
        breaker.failures = 3

        ok = await probe_provider(card)

        assert ok is True
        assert breaker.failures == 0

    async def test_5xx_response_records_failure(self, monkeypatch):
        card = make_card(
            id="broken",
            health_probe_url="https://example.invalid/health",
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1),
        )

        class _Resp:
            status_code = 502

        class _Client:
            def __init__(self, *a, **kw): ...
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): return _Resp()

        class _HttpxStub:
            AsyncClient = _Client

        monkeypatch.setattr(
            "builtins.__import__",
            _patched_import({"httpx": _HttpxStub}, original=__builtins__["__import__"]),
        )

        ok = await probe_provider(card)

        assert ok is False
        breaker = get_breaker(card)
        assert breaker.state == "open"

    async def test_exception_records_failure(self, monkeypatch):
        card = make_card(
            id="unreachable",
            health_probe_url="https://example.invalid/health",
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1),
        )

        class _Client:
            def __init__(self, *a, **kw): ...
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url):
                raise ConnectionError("dns failed")

        class _HttpxStub:
            AsyncClient = _Client

        monkeypatch.setattr(
            "builtins.__import__",
            _patched_import({"httpx": _HttpxStub}, original=__builtins__["__import__"]),
        )

        ok = await probe_provider(card)

        assert ok is False
        assert get_breaker(card).state == "open"


class TestSelectWithFallback:
    def test_walks_tier_order(self):
        matrix = [
            make_card(id="primary-a", tier="primary"),
            make_card(id="fallback-a", tier="fallback"),
            make_card(id="cheap-a", tier="cheap"),
        ]
        yielded = list(select_with_fallback(matrix, ["primary", "fallback"]))
        assert [c.id for c in yielded] == ["primary-a", "fallback-a"]

    def test_skips_open_breakers(self):
        matrix = [
            make_card(
                id="dead-primary",
                tier="primary",
                circuit_breaker=CircuitBreakerConfig(failure_threshold=1),
            ),
            make_card(id="alive-fallback", tier="fallback"),
        ]
        # Trip the primary's breaker.
        b = get_breaker(matrix[0])
        b.record_failure()
        assert b.state == "open"

        yielded = list(select_with_fallback(matrix, ["primary", "fallback"]))
        assert [c.id for c in yielded] == ["alive-fallback"]

    def test_empty_when_all_open(self):
        matrix = [
            make_card(
                id="dead",
                tier="primary",
                circuit_breaker=CircuitBreakerConfig(failure_threshold=1),
            ),
        ]
        get_breaker(matrix[0]).record_failure()
        yielded = list(select_with_fallback(matrix, ["primary"]))
        assert yielded == []

    def test_healthy_only_false_ignores_breakers(self):
        matrix = [
            make_card(
                id="dead",
                tier="primary",
                circuit_breaker=CircuitBreakerConfig(failure_threshold=1),
            ),
        ]
        get_breaker(matrix[0]).record_failure()
        yielded = list(
            select_with_fallback(matrix, ["primary"], healthy_only=False)
        )
        assert [c.id for c in yielded] == ["dead"]


def _patched_import(overrides, original):
    """Install module-override shim without touching real sys.modules."""
    def _imp(name, globals=None, locals=None, fromlist=(), level=0):
        if name in overrides and level == 0:
            return overrides[name]
        return original(name, globals, locals, fromlist, level)
    return _imp
