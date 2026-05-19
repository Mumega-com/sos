"""
Gateway Client with Failover Support

Provides reliable communication with the SOS Gateway, including:
- Primary/secondary URL failover
- Automatic retry with exponential backoff
- Circuit breaker integration
- Graceful shutdown support

Usage:
    from sos.kernel.gateway import GatewayClient

    client = GatewayClient()
    result = await client.request("memory_search", {"query": "test"})
"""

import os
import asyncio
import signal
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List, Callable
from datetime import datetime, timezone

from sos.observability.logging import get_logger
from sos.observability.metrics import (
    record_circuit_breaker_trip,
    record_circuit_breaker_failure,
    record_circuit_breaker_success,
    set_circuit_breaker_state,
)

log = get_logger("gateway_client")


@dataclass
class GatewayConfig:
    """Gateway client configuration."""
    primary_url: str = field(
        default_factory=lambda: os.getenv(
            "GATEWAY_URL",
            "https://gateway.mumega.com/"
        )
    )
    secondary_url: str = field(
        default_factory=lambda: os.getenv(
            "GATEWAY_SECONDARY_URL",
            "https://mcp-gateway.weathered-scene-2272.workers.dev/"
        )
    )
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0

    # Circuit breaker settings
    failure_threshold: int = 5
    recovery_timeout: float = 60.0


class CircuitState:
    """Circuit breaker state."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Simple circuit breaker for gateway requests."""
    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0

    failures: int = 0
    state: str = CircuitState.CLOSED
    last_failure_time: Optional[float] = None

    def record_success(self):
        """Record a successful call."""
        self.failures = 0
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            set_circuit_breaker_state(self.name, "closed")
            log.info(f"Circuit {self.name} closed after recovery")
        record_circuit_breaker_success(self.name)

    def record_failure(self):
        """Record a failed call."""
        self.failures += 1
        record_circuit_breaker_failure(self.name)

        import time
        self.last_failure_time = time.time()

        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            set_circuit_breaker_state(self.name, "open")
            record_circuit_breaker_trip(self.name)
            log.warn(f"Circuit {self.name} opened after {self.failures} failures")

    def can_execute(self) -> bool:
        """Check if circuit allows execution."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            import time
            elapsed = time.time() - (self.last_failure_time or 0)
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                set_circuit_breaker_state(self.name, "half_open")
                log.info(f"Circuit {self.name} entering half-open state")
                return True
            return False

        # Half-open: allow one request through
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Serialize state for persistence."""
        return {
            "name": self.name,
            "failures": self.failures,
            "state": self.state,
            "last_failure_time": self.last_failure_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CircuitBreaker":
        """Restore from persisted state."""
        cb = cls(name=data["name"])
        cb.failures = data.get("failures", 0)
        cb.state = data.get("state", CircuitState.CLOSED)
        cb.last_failure_time = data.get("last_failure_time")
        return cb


class GatewayClient:
    """
    Gateway client with failover and circuit breaker support.
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig()
        self.urls = [self.config.primary_url, self.config.secondary_url]
        self.current_url_index = 0

        # Circuit breakers for each URL
        self.circuit_breakers = {
            url: CircuitBreaker(
                name=f"gateway_{i}",
                failure_threshold=self.config.failure_threshold,
                recovery_timeout=self.config.recovery_timeout,
            )
            for i, url in enumerate(self.urls)
        }

        # Graceful shutdown
        self._shutting_down = False
        self._pending_requests: List[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()

        log.info(
            "GatewayClient initialized",
            primary=self.config.primary_url,
            secondary=self.config.secondary_url,
        )

    @property
    def current_url(self) -> str:
        """Get current active URL."""
        return self.urls[self.current_url_index]

    def _failover(self) -> bool:
        """Switch to next available URL. Returns True if failover occurred."""
        original = self.current_url_index
        for _ in range(len(self.urls)):
            self.current_url_index = (self.current_url_index + 1) % len(self.urls)
            url = self.current_url
            if self.circuit_breakers[url].can_execute():
                if self.current_url_index != original:
                    log.info(f"Failing over to {url}")
                return True
        return False

    async def request(
        self,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Make a request to the gateway with automatic failover.

        Args:
            action: Gateway action (e.g., "memory_search", "river_chat")
            payload: Request payload
            timeout: Optional timeout override

        Returns:
            Gateway response dict

        Raises:
            GatewayError: If all URLs fail
        """
        if self._shutting_down:
            raise GatewayError("Gateway client is shutting down")

        timeout = timeout or self.config.timeout
        last_error = None

        # Try each URL
        for attempt in range(len(self.urls)):
            url = self.current_url
            cb = self.circuit_breakers[url]

            if not cb.can_execute():
                self._failover()
                continue

            try:
                result = await self._do_request(url, action, payload, timeout)
                cb.record_success()
                return result

            except Exception as e:
                last_error = e
                cb.record_failure()
                log.warn(f"Gateway request failed: {url}", error=str(e))

                if not self._failover():
                    break

        raise GatewayError(f"All gateway URLs failed: {last_error}")

    async def _do_request(
        self,
        url: str,
        action: str,
        payload: Optional[Dict[str, Any]],
        timeout: float,
    ) -> Dict[str, Any]:
        """Execute single request with retry."""
        import httpx

        request_body = {
            "action": action,
            "payload": payload or {},
        }

        for attempt in range(self.config.max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        json=request_body,
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    return response.json()

            except httpx.TimeoutException:
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (self.config.retry_backoff ** attempt)
                    log.debug(f"Request timeout, retrying in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    raise

            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (self.config.retry_backoff ** attempt)
                    await asyncio.sleep(delay)
                else:
                    raise

        raise GatewayError(f"Max retries exceeded for {url}")

    def setup_signal_handlers(self):
        """Setup graceful shutdown signal handlers."""
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_shutdown(s))
            )

        log.info("Signal handlers registered for graceful shutdown")

    async def _handle_shutdown(self, sig: signal.Signals):
        """Handle shutdown signal."""
        log.info(f"Received signal {sig.name}, initiating graceful shutdown")
        self._shutting_down = True

        # Wait for pending requests
        if self._pending_requests:
            log.info(f"Waiting for {len(self._pending_requests)} pending requests")
            await asyncio.gather(*self._pending_requests, return_exceptions=True)

        # Persist circuit breaker state
        await self._persist_circuit_breaker_state()

        self._shutdown_event.set()
        log.info("Graceful shutdown complete")

    async def wait_for_shutdown(self):
        """Wait for shutdown to complete."""
        await self._shutdown_event.wait()

    async def _persist_circuit_breaker_state(self):
        """Persist circuit breaker state to storage."""
        try:
            import json
            state = {
                url: cb.to_dict()
                for url, cb in self.circuit_breakers.items()
            }

            # Try Redis first
            try:
                import redis.asyncio as redis
                from sos.kernel.settings import get_settings as _get_settings
                r = redis.from_url(_get_settings().redis.resolved_url)
                await r.set("sos:gateway:circuit_breakers", json.dumps(state))
                await r.close()
                log.info("Circuit breaker state persisted to Redis")
                return
            except Exception:
                pass

            # Fallback to file
            state_file = os.path.expanduser("~/.sos/gateway_state.json")
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            with open(state_file, "w") as f:
                json.dump(state, f)
            log.info(f"Circuit breaker state persisted to {state_file}")

        except Exception as e:
            log.error(f"Failed to persist circuit breaker state: {e}")

    async def restore_circuit_breaker_state(self):
        """Restore circuit breaker state from storage."""
        try:
            import json

            # Try Redis first
            try:
                import redis.asyncio as redis
                from sos.kernel.settings import get_settings as _get_settings
                r = redis.from_url(_get_settings().redis.resolved_url)
                data = await r.get("sos:gateway:circuit_breakers")
                await r.close()
                if data:
                    state = json.loads(data)
                    self._apply_circuit_breaker_state(state)
                    log.info("Circuit breaker state restored from Redis")
                    return
            except Exception:
                pass

            # Fallback to file
            state_file = os.path.expanduser("~/.sos/gateway_state.json")
            if os.path.exists(state_file):
                with open(state_file) as f:
                    state = json.load(f)
                self._apply_circuit_breaker_state(state)
                log.info(f"Circuit breaker state restored from {state_file}")

        except Exception as e:
            log.warn(f"Failed to restore circuit breaker state: {e}")

    def _apply_circuit_breaker_state(self, state: Dict[str, Dict]):
        """Apply restored state to circuit breakers."""
        for url, cb_data in state.items():
            if url in self.circuit_breakers:
                cb = self.circuit_breakers[url]
                cb.failures = cb_data.get("failures", 0)
                cb.state = cb_data.get("state", CircuitState.CLOSED)
                cb.last_failure_time = cb_data.get("last_failure_time")

    def health(self) -> Dict[str, Any]:
        """Return client health status."""
        return {
            "current_url": self.current_url,
            "shutting_down": self._shutting_down,
            "circuit_breakers": {
                url: {
                    "state": cb.state,
                    "failures": cb.failures,
                }
                for url, cb in self.circuit_breakers.items()
            },
        }


class GatewayError(Exception):
    """Gateway communication error."""
    pass


# Singleton client instance
_client: Optional[GatewayClient] = None


def get_gateway_client() -> GatewayClient:
    """Get or create the singleton gateway client."""
    global _client
    if _client is None:
        _client = GatewayClient()
    return _client


async def gateway_request(
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """Convenience function for gateway requests."""
    client = get_gateway_client()
    return await client.request(action, payload, **kwargs)
