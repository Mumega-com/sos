"""
Autonomy Coordinator
====================

Manages autonomy services for multiple agents.
Provides centralized control and monitoring.
"""

import asyncio
from typing import Dict, List, Optional, Any

from sos.observability.logging import get_logger
from sos.services.autonomy.service import AutonomyService, AutonomyConfig

log = get_logger("autonomy_coordinator")


class AutonomyCoordinator:
    """
    Coordinates autonomy services for multiple agents.

    Provides:
    - Centralized start/stop
    - Health monitoring
    - Event aggregation
    """

    def __init__(self):
        self.services: Dict[str, AutonomyService] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.running = False

    def register(
        self,
        agent_id: str,
        config: Optional[AutonomyConfig] = None
    ) -> AutonomyService:
        """
        Register an agent for autonomy.

        Args:
            agent_id: Agent identifier
            config: Autonomy configuration

        Returns:
            AutonomyService instance
        """
        if agent_id in self.services:
            log.warn(f"Agent {agent_id} already registered, replacing")

        config = config or AutonomyConfig(agent_id=agent_id)
        service = AutonomyService(
            agent_id=agent_id,
            config=config,
            on_event=lambda event_type, data: self._handle_event(agent_id, event_type, data)
        )

        self.services[agent_id] = service
        log.info(f"Registered autonomy for {agent_id}")

        return service

    def unregister(self, agent_id: str):
        """Unregister an agent."""
        if agent_id in self.services:
            del self.services[agent_id]
            if agent_id in self.tasks:
                self.tasks[agent_id].cancel()
                del self.tasks[agent_id]
            log.info(f"Unregistered autonomy for {agent_id}")

    async def start(self, agent_ids: List[str] = None):
        """
        Start autonomy for specified agents or all registered.

        Args:
            agent_ids: List of agent IDs to start (None = all)
        """
        self.running = True
        agents = agent_ids or list(self.services.keys())

        for agent_id in agents:
            if agent_id not in self.services:
                log.warn(f"Agent {agent_id} not registered")
                continue

            service = self.services[agent_id]
            task = asyncio.create_task(service.start())
            self.tasks[agent_id] = task

        log.info(f"Started autonomy for {len(agents)} agents")

    async def stop(self, agent_ids: List[str] = None):
        """
        Stop autonomy for specified agents or all.

        Args:
            agent_ids: List of agent IDs to stop (None = all)
        """
        agents = agent_ids or list(self.services.keys())

        for agent_id in agents:
            if agent_id in self.services:
                await self.services[agent_id].stop()

            if agent_id in self.tasks:
                self.tasks[agent_id].cancel()
                try:
                    await self.tasks[agent_id]
                except asyncio.CancelledError:
                    pass
                del self.tasks[agent_id]

        if not agent_ids:
            self.running = False

        log.info(f"Stopped autonomy for {len(agents)} agents")

    async def health(self) -> Dict[str, Any]:
        """Get health status of all services."""
        statuses = {}
        for agent_id, service in self.services.items():
            try:
                statuses[agent_id] = await service.health()
            except Exception as e:
                statuses[agent_id] = {"status": "error", "error": str(e)}

        return {
            "coordinator_running": self.running,
            "agent_count": len(self.services),
            "active_tasks": len(self.tasks),
            "agents": statuses
        }

    def _handle_event(self, agent_id: str, event_type: str, data: Dict[str, Any]):
        """Handle events from autonomy services."""
        log.debug(f"Event from {agent_id}: {event_type}", data=data)
        # Could forward to message bus, store metrics, etc.


# Default coordinator instance
_coordinator: Optional[AutonomyCoordinator] = None


def get_coordinator() -> AutonomyCoordinator:
    """Get or create the default coordinator."""
    global _coordinator
    if _coordinator is None:
        _coordinator = AutonomyCoordinator()
    return _coordinator


# Convenience functions
async def start_autonomy(agents: List[str] = None):
    """Start autonomy for agents."""
    coordinator = get_coordinator()

    # Register default agents if none specified
    if not coordinator.services:
        coordinator.register("agent:River", AutonomyConfig(
            agent_id="agent:River",
            agent_name="River",
            enable_social=False
        ))

    await coordinator.start(agents)


async def stop_autonomy(agents: List[str] = None):
    """Stop autonomy for agents."""
    await get_coordinator().stop(agents)
