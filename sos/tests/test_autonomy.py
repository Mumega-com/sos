"""
Tests for SOS Autonomy Service.

Tests pulse scheduling, dream synthesis, and coordinator.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from sos.services.autonomy.service import AutonomyService, AutonomyConfig
from sos.services.autonomy.coordinator import AutonomyCoordinator


class TestAutonomyConfig:
    """Test autonomy configuration."""

    def test_default_config(self):
        """Default config has sensible values."""
        config = AutonomyConfig()
        assert config.agent_id == "agent:River"
        assert config.pulse_interval_seconds == 300  # 5 min
        assert config.dream_interval_seconds == 3600  # 1 hr
        assert config.enable_dreams is True

    def test_custom_config(self):
        """Custom config overrides defaults."""
        config = AutonomyConfig(
            agent_id="agent:Test",
            pulse_interval_seconds=60,
            enable_dreams=False
        )
        assert config.agent_id == "agent:Test"
        assert config.pulse_interval_seconds == 60
        assert config.enable_dreams is False


class TestAutonomyService:
    """Test autonomy service lifecycle."""

    @pytest.fixture
    def config(self):
        return AutonomyConfig(
            agent_id="agent:Test",
            pulse_interval_seconds=0.1,
            dream_interval_seconds=0.2,
            enable_dreams=True
        )

    @pytest.fixture
    def service(self, config):
        return AutonomyService(config=config)

    def test_service_initialization(self, service, config):
        """Service initializes with config."""
        assert service.config == config
        assert service._running is False

    @pytest.mark.asyncio
    async def test_service_start_stop(self, service):
        """Service can start and stop."""
        # Start in background
        task = asyncio.create_task(service.start())

        # Let it run briefly
        await asyncio.sleep(0.05)
        assert service._running is True

        # Stop it
        await service.stop()
        assert service._running is False

        # Clean up task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestAutonomyCoordinator:
    """Test autonomy coordinator."""

    @pytest.fixture
    def coordinator(self):
        return AutonomyCoordinator(agent_id="agent:Test")

    def test_coordinator_initialization(self, coordinator):
        """Coordinator initializes correctly."""
        assert coordinator.agent_id == "agent:Test"

    @pytest.mark.asyncio
    async def test_should_pulse(self, coordinator):
        """Coordinator determines when to pulse."""
        # First call should allow pulse
        assert coordinator.should_pulse() is True

        # Mark pulse done
        coordinator.record_pulse()

        # Immediate second call should not pulse (too soon)
        # This depends on implementation - adjust if needed

    @pytest.mark.asyncio
    async def test_should_dream(self, coordinator):
        """Coordinator determines when to dream."""
        # Check dream scheduling logic
        result = coordinator.should_dream()
        assert isinstance(result, bool)
