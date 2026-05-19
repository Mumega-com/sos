"""
Tests for Gateway client with failover support.
"""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from sos.kernel.gateway import (
    GatewayClient,
    GatewayConfig,
    GatewayError,
    CircuitBreaker,
    CircuitState,
    get_gateway_client,
    gateway_request,
)


class TestCircuitBreaker:
    """Tests for circuit breaker."""

    def test_initial_state_closed(self):
        """Circuit should start closed."""
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED
        assert cb.failures == 0
        assert cb.can_execute() is True

    def test_records_success(self):
        """Success should reset failures."""
        cb = CircuitBreaker(name="test")
        cb.failures = 3
        cb.record_success()
        assert cb.failures == 0

    def test_opens_after_threshold(self):
        """Circuit should open after failure threshold."""
        cb = CircuitBreaker(name="test", failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_half_open_after_recovery_timeout(self):
        """Circuit should enter half-open after recovery timeout."""
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.1)

        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

        # Wait for recovery
        import time
        time.sleep(0.15)

        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_on_success_from_half_open(self):
        """Circuit should close on success from half-open."""
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0)
        cb.record_failure()
        cb.can_execute()  # Enter half-open

        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_serialization(self):
        """Circuit should serialize and deserialize."""
        cb = CircuitBreaker(name="test")
        cb.failures = 2
        cb.state = CircuitState.OPEN
        cb.last_failure_time = 12345.0

        data = cb.to_dict()
        restored = CircuitBreaker.from_dict(data)

        assert restored.name == "test"
        assert restored.failures == 2
        assert restored.state == CircuitState.OPEN
        assert restored.last_failure_time == 12345.0


class TestGatewayClient:
    """Tests for gateway client."""

    def test_initialization(self):
        """Client should initialize with config."""
        config = GatewayConfig(
            primary_url="https://primary.test/",
            secondary_url="https://secondary.test/",
        )
        client = GatewayClient(config)

        assert client.current_url == "https://primary.test/"
        assert len(client.circuit_breakers) == 2

    def test_failover_switches_url(self):
        """Failover should switch to secondary URL."""
        config = GatewayConfig(
            primary_url="https://primary.test/",
            secondary_url="https://secondary.test/",
        )
        client = GatewayClient(config)

        # Open primary circuit
        cb = client.circuit_breakers["https://primary.test/"]
        for _ in range(5):
            cb.record_failure()

        # Should failover
        result = client._failover()
        assert result is True
        assert client.current_url == "https://secondary.test/"

    def test_failover_returns_false_when_all_open(self):
        """Failover should return False when all circuits open."""
        config = GatewayConfig(
            primary_url="https://primary.test/",
            secondary_url="https://secondary.test/",
        )
        client = GatewayClient(config)

        # Open all circuits
        for cb in client.circuit_breakers.values():
            for _ in range(5):
                cb.record_failure()

        result = client._failover()
        assert result is False

    @pytest.mark.asyncio
    async def test_request_success(self):
        """Successful request should return response."""
        client = GatewayClient()

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"success": True, "result": "data"}
            mock_response.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await client.request("test_action", {"key": "value"})

            assert result == {"success": True, "result": "data"}
            mock_instance.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_fails_over_on_error(self):
        """Request should failover on error."""
        config = GatewayConfig(
            primary_url="https://primary.test/",
            secondary_url="https://secondary.test/",
            max_retries=1,
        )
        client = GatewayClient(config)

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Primary failed")
            response = MagicMock()
            response.json.return_value = {"success": True}
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await client.request("test")
            assert result == {"success": True}
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_request_raises_when_all_fail(self):
        """Request should raise GatewayError when all URLs fail."""
        config = GatewayConfig(
            primary_url="https://primary.test/",
            secondary_url="https://secondary.test/",
            max_retries=1,
        )
        client = GatewayClient(config)

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = Exception("All failed")
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(GatewayError, match="All gateway URLs failed"):
                await client.request("test")

    @pytest.mark.asyncio
    async def test_request_rejected_during_shutdown(self):
        """Request should be rejected during shutdown."""
        client = GatewayClient()
        client._shutting_down = True

        with pytest.raises(GatewayError, match="shutting down"):
            await client.request("test")

    def test_health_returns_status(self):
        """Health should return client status."""
        client = GatewayClient()
        health = client.health()

        assert "current_url" in health
        assert "shutting_down" in health
        assert "circuit_breakers" in health
        assert health["shutting_down"] is False


class TestCircuitBreakerPersistence:
    """Tests for circuit breaker state persistence."""

    @pytest.mark.asyncio
    async def test_persist_to_file(self):
        """Should persist state to file when Redis unavailable."""
        import tempfile
        import os
        import json

        client = GatewayClient()

        # Set some state
        cb = list(client.circuit_breakers.values())[0]
        cb.failures = 3

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, ".sos", "gateway_state.json")

            # Mock Redis to fail, forcing file fallback
            with patch("redis.asyncio.from_url") as mock_redis:
                mock_redis.side_effect = Exception("Redis unavailable")

                with patch("os.path.expanduser", return_value=state_file):
                    await client._persist_circuit_breaker_state()

                    # Verify file was created
                    assert os.path.exists(state_file)
                    with open(state_file) as f:
                        saved = json.load(f)
                    assert len(saved) == 2  # Two URLs

    @pytest.mark.asyncio
    async def test_restore_from_file(self):
        """Should restore state from file."""
        import tempfile
        import json
        import os

        config = GatewayConfig(
            primary_url="https://test.example.com/",
            secondary_url="https://test2.example.com/",
        )
        client = GatewayClient(config)

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, ".sos", "gateway_state.json")
            os.makedirs(os.path.dirname(state_file), exist_ok=True)

            # Create state file matching client's URLs
            state = {
                "https://test.example.com/": {
                    "name": "gateway_0",
                    "failures": 3,
                    "state": "open",
                    "last_failure_time": 12345.0,
                }
            }
            with open(state_file, "w") as f:
                json.dump(state, f)

            # Mock Redis to fail, forcing file fallback
            with patch("redis.asyncio.from_url") as mock_redis:
                mock_redis.side_effect = Exception("Redis unavailable")

                with patch("os.path.expanduser", return_value=state_file):
                    await client.restore_circuit_breaker_state()

            # Verify state was restored
            cb = client.circuit_breakers.get("https://test.example.com/")
            assert cb is not None
            assert cb.failures == 3
            assert cb.state == "open"


class TestSingletonClient:
    """Tests for singleton client access."""

    def test_get_gateway_client_returns_same_instance(self):
        """get_gateway_client should return same instance."""
        # Reset singleton
        import sos.kernel.gateway as gateway_module
        gateway_module._client = None

        client1 = get_gateway_client()
        client2 = get_gateway_client()

        assert client1 is client2

    @pytest.mark.asyncio
    async def test_gateway_request_convenience_function(self):
        """gateway_request should use singleton client."""
        import sos.kernel.gateway as gateway_module
        gateway_module._client = None

        with patch.object(GatewayClient, "request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            result = await gateway_request("test", {"key": "value"})

            assert result == {"success": True}
            mock_request.assert_called_once_with("test", {"key": "value"})
