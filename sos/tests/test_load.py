"""
Load tests for SOS resilience components.

Tests:
- Burst traffic for rate limiter
- Sustained load for RPM limits
- Failure injection for circuit breaker
- Recovery testing

Run with: pytest sos/tests/test_load.py -v --timeout=60
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from sos.services.engine.resilience import CircuitBreaker, RateLimiter, CircuitState


class TestRateLimiterLoad:
    """Load tests for rate limiter."""

    @pytest.mark.asyncio
    async def test_burst_traffic(self):
        """Rate limiter should handle burst traffic correctly."""
        limiter = RateLimiter(name="test", requests_per_minute=600, burst_capacity=5)

        # Burst should allow up to burst_capacity immediately
        allowed = 0
        for _ in range(10):
            if await limiter.acquire(timeout=0.01):
                allowed += 1

        # Should allow burst_capacity requests
        assert allowed == 5, f"Expected 5 allowed in burst, got {allowed}"

    @pytest.mark.asyncio
    async def test_sustained_rpm_limit(self):
        """Rate limiter should enforce RPM over time."""
        # 60 RPM = 1 per second
        limiter = RateLimiter(name="test", requests_per_minute=60, burst_capacity=1)

        # First request should succeed
        assert await limiter.acquire(timeout=0.1) is True

        # Immediate second request should fail (no time to refill)
        assert await limiter.acquire(timeout=0.1) is False

        # Wait for token refill (slightly over 1 second)
        await asyncio.sleep(1.1)

        # Now should succeed again
        assert await limiter.acquire(timeout=0.1) is True

    @pytest.mark.asyncio
    async def test_concurrent_rate_limiting(self):
        """Rate limiter should work correctly under concurrent access."""
        limiter = RateLimiter(name="test", requests_per_minute=6000, burst_capacity=20)

        results = []
        lock = asyncio.Lock()

        async def make_request():
            result = await limiter.acquire(timeout=0.01)
            async with lock:
                results.append(result)

        # Launch 50 concurrent requests
        tasks = [make_request() for _ in range(50)]
        await asyncio.gather(*tasks)

        allowed = sum(results)
        assert allowed <= 25, f"Should not exceed burst + some refill, got {allowed}"
        assert allowed >= 15, f"Should allow reasonable amount, got {allowed}"


class TestCircuitBreakerLoad:
    """Load tests for circuit breaker."""

    @pytest.mark.asyncio
    async def test_failure_threshold_under_load(self):
        """Circuit breaker should trip after threshold under concurrent failures."""
        cb = CircuitBreaker(name="test", failure_threshold=5, reset_timeout=1.0)

        failure_count = 0
        lock = asyncio.Lock()

        async def failing_request():
            nonlocal failure_count
            if cb.can_execute():
                cb.record_failure()
                async with lock:
                    failure_count += 1

        # Launch 20 concurrent failing requests
        tasks = [failing_request() for _ in range(20)]
        await asyncio.gather(*tasks)

        # Circuit should be open
        assert cb.state == CircuitState.OPEN
        # Should have recorded at least threshold failures before opening
        assert failure_count >= 5

    @pytest.mark.asyncio
    async def test_recovery_under_load(self):
        """Circuit breaker should recover correctly under load."""
        cb = CircuitBreaker(name="test", failure_threshold=3, reset_timeout=0.5)

        # Trip the circuit
        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.6)

        # Should be half-open, one request allowed
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

        # Success should close circuit
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

        # Now concurrent requests should all succeed
        success_count = 0
        lock = asyncio.Lock()

        async def success_request():
            nonlocal success_count
            if cb.can_execute():
                cb.record_success()
                async with lock:
                    success_count += 1

        tasks = [success_request() for _ in range(10)]
        await asyncio.gather(*tasks)

        assert success_count == 10

    @pytest.mark.asyncio
    async def test_mixed_success_failure_pattern(self):
        """Circuit breaker should handle mixed success/failure patterns."""
        cb = CircuitBreaker(name="test", failure_threshold=5, reset_timeout=0.5)

        # Pattern: 3 success, 2 fail - successes reset failure count
        for _ in range(3):
            # Successes
            for _ in range(3):
                if cb.can_execute():
                    cb.record_success()

            # Failures
            for _ in range(2):
                if cb.can_execute():
                    cb.record_failure()

        # Circuit should still be closed (successes reset failure count)
        assert cb.state == CircuitState.CLOSED

    def test_rapid_state_transitions(self):
        """Circuit breaker should handle rapid state transitions."""
        cb = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=0.1)

        transitions = []

        for cycle in range(5):
            # Trip it
            cb.record_failure()
            cb.record_failure()
            transitions.append(cb.state)

            # Wait for recovery
            time.sleep(0.15)
            cb.can_execute()  # Enter half-open
            transitions.append(cb.state)

            # Close it
            cb.record_success()
            transitions.append(cb.state)

        # Should have gone through: OPEN -> HALF_OPEN -> CLOSED five times
        opens = transitions.count(CircuitState.OPEN)
        half_opens = transitions.count(CircuitState.HALF_OPEN)
        closed = transitions.count(CircuitState.CLOSED)

        assert opens == 5, f"Expected 5 OPEN states, got {opens}"
        assert half_opens == 5, f"Expected 5 HALF_OPEN states, got {half_opens}"
        assert closed == 5, f"Expected 5 CLOSED states, got {closed}"


class TestCombinedResilience:
    """Tests for combined rate limiter + circuit breaker."""

    @pytest.mark.asyncio
    async def test_rate_limiter_with_circuit_breaker(self):
        """Combined rate limiting and circuit breaking should work together."""
        limiter = RateLimiter(name="test", requests_per_minute=6000, burst_capacity=10)
        cb = CircuitBreaker(name="test", failure_threshold=3, reset_timeout=1.0)

        allowed = 0
        blocked_by_limiter = 0
        blocked_by_circuit = 0
        failures = 0

        async def make_request(should_fail: bool):
            nonlocal allowed, blocked_by_limiter, blocked_by_circuit, failures

            # Check circuit breaker first
            if not cb.can_execute():
                blocked_by_circuit += 1
                return

            # Then rate limiter
            if not await limiter.acquire(timeout=0.01):
                blocked_by_limiter += 1
                return

            allowed += 1

            # Simulate failure
            if should_fail:
                cb.record_failure()
                failures += 1
            else:
                cb.record_success()

        # Make 20 requests, first 5 fail
        tasks = [make_request(i < 5) for i in range(20)]
        await asyncio.gather(*tasks)

        # Should have some allowed, some blocked
        assert allowed > 0, "Should have allowed some requests"
        assert failures >= 3, "Should have recorded failures"

        # Circuit should be open after 3 failures
        assert cb.state == CircuitState.OPEN


class TestStressPatterns:
    """Stress test patterns for resilience components."""

    @pytest.mark.asyncio
    async def test_gradual_degradation(self):
        """Test gradual degradation under increasing load."""
        import random

        # At 90% failure rate with 20 requests, expect ~18 failures
        # Circuit should trip with threshold=10
        cb = CircuitBreaker(name="test", failure_threshold=10, reset_timeout=1.0)

        # Make 30 requests with 90% failure rate
        for _ in range(30):
            if cb.can_execute():
                if random.random() < 0.9:
                    cb.record_failure()
                else:
                    cb.record_success()

        # Should have tripped
        assert cb.state == CircuitState.OPEN, "Should trip at 90% failure rate with 30 requests"

    @pytest.mark.asyncio
    async def test_token_refill_over_time(self):
        """Test that tokens refill correctly over time."""
        limiter = RateLimiter(name="test", requests_per_minute=60, burst_capacity=3)

        # Exhaust burst tokens
        for _ in range(3):
            assert await limiter.acquire(timeout=0.01) is True

        # Should be exhausted
        assert await limiter.acquire(timeout=0.01) is False

        # Wait for 2 seconds (should refill ~2 tokens at 60 RPM = 1/sec)
        await asyncio.sleep(2.1)

        # Should have tokens again
        assert await limiter.acquire(timeout=0.1) is True
        assert await limiter.acquire(timeout=0.1) is True


class TestEdgeCases:
    """Edge case tests for resilience components."""

    @pytest.mark.asyncio
    async def test_very_high_rpm(self):
        """Rate limiter should handle very high RPM."""
        limiter = RateLimiter(name="test", requests_per_minute=100000, burst_capacity=100)

        # Should allow many requests quickly
        results = []
        for _ in range(100):
            results.append(await limiter.acquire(timeout=0.01))
        allowed = sum(results)
        assert allowed == 100, f"High RPM limiter should allow burst, got {allowed}"

    def test_circuit_breaker_immediate_trip(self):
        """Circuit breaker with threshold=1 should trip immediately."""
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=0.1)

        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_circuit_breaker_high_threshold(self):
        """Circuit breaker with high threshold should be permissive."""
        cb = CircuitBreaker(name="test", failure_threshold=1000, reset_timeout=1.0)

        for _ in range(100):
            cb.record_failure()

        # Still should be closed (threshold not reached)
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True
