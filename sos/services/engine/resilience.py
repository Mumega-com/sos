"""
Engine Resilience - Circuit Breaker, Rate Limiting, and Failover
=================================================================

Provides fault tolerance patterns for SOS Engine model routing.

Patterns Implemented:
- Circuit Breaker: Trip after N failures, reset after cooldown
- Rate Limiter: Token bucket with per-adapter limits
- Failover Router: Cascade through adapters on failure

Usage:
    from sos.services.engine.resilience import (
        CircuitBreaker, RateLimiter, ResilientRouter
    )

    router = ResilientRouter(adapters={...})
    response = await router.generate(prompt)
"""

import time
import asyncio
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field

from sos.observability.logging import get_logger

log = get_logger("engine_resilience")


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for model adapters.

    Trips to OPEN after `failure_threshold` consecutive failures.
    After `reset_timeout` seconds, enters HALF_OPEN to test recovery.
    """
    name: str
    failure_threshold: int = 3
    reset_timeout: float = 60.0
    half_open_max_calls: int = 1

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    half_open_calls: int = 0

    def can_execute(self) -> bool:
        """Check if circuit allows execution."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if reset timeout has passed
            if time.time() - self.last_failure_time >= self.reset_timeout:
                self._transition_to_half_open()
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # Allow limited calls in half-open state
            return self.half_open_calls < self.half_open_max_calls

        return False

    def record_success(self):
        """Record a successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.half_open_max_calls:
                self._transition_to_closed()
        else:
            self.failure_count = 0

    def record_failure(self):
        """Record a failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            self._transition_to_open()
        elif self.failure_count >= self.failure_threshold:
            self._transition_to_open()

    def _transition_to_open(self):
        log.warn(f"Circuit OPEN: {self.name} (failures: {self.failure_count})")
        self.state = CircuitState.OPEN
        self.half_open_calls = 0
        self.success_count = 0

    def _transition_to_half_open(self):
        log.info(f"Circuit HALF_OPEN: {self.name} (testing recovery)")
        self.state = CircuitState.HALF_OPEN
        self.half_open_calls = 0
        self.success_count = 0

    def _transition_to_closed(self):
        log.info(f"Circuit CLOSED: {self.name} (recovered)")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure": self.last_failure_time,
        }


@dataclass
class RateLimiter:
    """
    Token bucket rate limiter for model adapters.

    Enforces requests_per_minute limit with burst capacity.
    """
    name: str
    requests_per_minute: int = 60
    burst_capacity: int = 10

    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self):
        self._tokens = float(self.burst_capacity)
        self._last_refill = time.time()

    async def acquire(self, timeout: float = 5.0) -> bool:
        """
        Attempt to acquire a token. Returns True if allowed.

        Args:
            timeout: Max wait time for a token (seconds)

        Returns:
            True if request can proceed, False if rate limited
        """
        start_time = time.time()

        async with self._lock:
            while True:
                self._refill_tokens()

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True

                # Calculate wait time for next token
                tokens_needed = 1.0 - self._tokens
                refill_rate = self.requests_per_minute / 60.0
                wait_time = tokens_needed / refill_rate

                # Check timeout
                elapsed = time.time() - start_time
                if elapsed + wait_time > timeout:
                    log.warn(f"Rate limit timeout: {self.name}")
                    return False

                # Wait and retry
                await asyncio.sleep(min(wait_time, 0.1))

    def _refill_tokens(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_refill
        refill_rate = self.requests_per_minute / 60.0
        tokens_to_add = elapsed * refill_rate

        self._tokens = min(self._tokens + tokens_to_add, float(self.burst_capacity))
        self._last_refill = now

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "tokens_available": int(self._tokens),
            "requests_per_minute": self.requests_per_minute,
        }


# Default rate limits per model type (RPM)
DEFAULT_RATE_LIMITS = {
    "gemini-3-flash-preview": 60,
    "grok-4-1-fast-reasoning": 30,
    "local": 120,
    "local-code": 120,
    "local-reasoning": 120,
    "sos-mock-v1": 1000,
}


class ResilientRouter:
    """
    Resilient model router with circuit breakers, rate limiting, and failover.

    Routes requests through adapters with automatic failover on errors.
    """

    def __init__(
        self,
        adapters: Dict[str, Any],
        fallback_chain: List[str] = None,
        rate_limits: Dict[str, int] = None,
    ):
        """
        Initialize resilient router.

        Args:
            adapters: Dict of model_id -> ModelAdapter
            fallback_chain: Ordered list of model_ids to try on failure
            rate_limits: Custom rate limits per model (RPM)
        """
        self.adapters = adapters
        self.fallback_chain = fallback_chain or list(adapters.keys())

        # Initialize circuit breakers and rate limiters
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.rate_limiters: Dict[str, RateLimiter] = {}

        limits = {**DEFAULT_RATE_LIMITS, **(rate_limits or {})}

        for model_id in adapters:
            self.circuit_breakers[model_id] = CircuitBreaker(name=model_id)
            rpm = limits.get(model_id, 60)
            self.rate_limiters[model_id] = RateLimiter(
                name=model_id,
                requests_per_minute=rpm,
                burst_capacity=max(5, rpm // 10)
            )

        log.info(
            "ResilientRouter initialized",
            models=list(adapters.keys()),
            fallback_chain=self.fallback_chain
        )

    async def generate(
        self,
        prompt: str,
        preferred_model: str = None,
        system_prompt: str = None,
        tools: List[Dict] = None,
        user_id: str = "default",
        history: List[Dict] = None,
        **kwargs
    ) -> tuple[str, str]:
        """
        Generate response with resilience.

        Args:
            prompt: The user's message
            preferred_model: Model to try first
            system_prompt: System instructions
            tools: Available tools
            user_id: User identifier for cache isolation
            history: Conversation history for cache optimization

        Returns:
            Tuple of (response_text, model_id_used)
        """
        # Build execution order: preferred first, then fallback chain
        execution_order = []
        if preferred_model and preferred_model in self.adapters:
            execution_order.append(preferred_model)

        for model_id in self.fallback_chain:
            if model_id not in execution_order:
                execution_order.append(model_id)

        last_error = None

        for model_id in execution_order:
            adapter = self.adapters.get(model_id)
            if not adapter:
                continue

            circuit = self.circuit_breakers.get(model_id)
            limiter = self.rate_limiters.get(model_id)

            # Check circuit breaker
            if circuit and not circuit.can_execute():
                log.debug(f"Circuit open, skipping: {model_id}")
                continue

            # Check rate limit
            if limiter:
                can_proceed = await limiter.acquire(timeout=2.0)
                if not can_proceed:
                    log.debug(f"Rate limited, trying next: {model_id}")
                    continue

            # Attempt generation
            try:
                log.debug(f"Attempting generation with: {model_id}")

                response = await adapter.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    tools=tools,
                    user_id=user_id,
                    history=history,
                    **kwargs
                )

                if circuit:
                    circuit.record_success()

                log.info(f"Generation successful: {model_id}")
                return response, model_id

            except Exception as e:
                last_error = e
                log.warn(f"Generation failed: {model_id}", error=str(e))

                if circuit:
                    circuit.record_failure()

                # Check if recoverable (rate limit vs fatal)
                error_str = str(e).lower()
                if "429" in error_str or "rate" in error_str or "exhausted" in error_str:
                    # Rate limit - try next adapter
                    continue
                elif "api" in error_str or "key" in error_str or "auth" in error_str:
                    # Auth error - skip this adapter entirely
                    log.error(f"Auth error for {model_id}, removing from rotation")
                    continue
                else:
                    # Other error - still try fallback
                    continue

        # All adapters failed
        error_msg = f"All models failed. Last error: {last_error}"
        log.error(error_msg)
        return error_msg, "error"

    def get_health(self) -> Dict[str, Any]:
        """Get health status of all adapters."""
        return {
            "circuits": {
                model_id: cb.get_status()
                for model_id, cb in self.circuit_breakers.items()
            },
            "rate_limiters": {
                model_id: rl.get_status()
                for model_id, rl in self.rate_limiters.items()
            },
            "fallback_chain": self.fallback_chain,
        }

    def reset_circuit(self, model_id: str):
        """Manually reset a circuit breaker."""
        if model_id in self.circuit_breakers:
            self.circuit_breakers[model_id]._transition_to_closed()
            log.info(f"Circuit manually reset: {model_id}")

    async def generate_stream(
        self,
        prompt: str,
        preferred_model: str = None,
        system_prompt: str = None,
        user_id: str = "default",
        **kwargs
    ) -> tuple[Any, str]:
        """
        Stream response tokens with resilience.

        Returns:
            Tuple of (async_iterator, model_id_used)
        """
        # Build execution order: preferred first, then fallback chain
        execution_order = []
        if preferred_model and preferred_model in self.adapters:
            execution_order.append(preferred_model)

        for model_id in self.fallback_chain:
            if model_id not in execution_order:
                execution_order.append(model_id)

        last_error = None

        for model_id in execution_order:
            adapter = self.adapters.get(model_id)
            if not adapter:
                continue

            circuit = self.circuit_breakers.get(model_id)
            limiter = self.rate_limiters.get(model_id)

            # Check circuit breaker
            if circuit and not circuit.can_execute():
                log.debug(f"Circuit open, skipping: {model_id}")
                continue

            # Check rate limit
            if limiter:
                can_proceed = await limiter.acquire(timeout=2.0)
                if not can_proceed:
                    log.debug(f"Rate limited, trying next: {model_id}")
                    continue

            # Attempt streaming
            try:
                log.debug(f"Attempting stream with: {model_id}")

                # Check if adapter has generate_stream
                if hasattr(adapter, 'generate_stream'):
                    stream = adapter.generate_stream(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        user_id=user_id,
                    )

                    if circuit:
                        circuit.record_success()

                    log.info(f"Streaming from: {model_id}")
                    return stream, model_id
                else:
                    # Fallback to non-streaming
                    log.debug(f"Adapter {model_id} has no streaming, using generate()")
                    response = await adapter.generate(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        user_id=user_id,
                        **kwargs
                    )

                    async def _fake_stream():
                        yield response

                    if circuit:
                        circuit.record_success()

                    return _fake_stream(), model_id

            except Exception as e:
                last_error = e
                log.warn(f"Stream failed: {model_id}", error=str(e))

                if circuit:
                    circuit.record_failure()
                continue

        # All adapters failed - return error stream
        async def _error_stream():
            yield f"Error: All models failed. Last error: {last_error}"

        return _error_stream(), "error"


# Decorator for resilient function calls
def with_retry(
    max_attempts: int = 3,
    backoff_base: float = 1.0,
    backoff_max: float = 30.0,
    retryable_exceptions: tuple = (Exception,)
):
    """
    Decorator for retry with exponential backoff.

    Usage:
        @with_retry(max_attempts=3)
        async def my_function():
            ...
    """
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = min(backoff_base * (2 ** attempt), backoff_max)
                        log.warn(
                            f"Retry {attempt + 1}/{max_attempts} after {delay:.1f}s",
                            error=str(e)
                        )
                        await asyncio.sleep(delay)

            raise last_exception

        return wrapper
    return decorator
