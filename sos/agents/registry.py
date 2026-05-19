"""
SOS Agent Registry - Discovery and management of agents.

The registry maintains the list of known agents and their current status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict
from enum import Enum

from sos.agents.definitions import AgentSoul, ALL_AGENTS, AgentRole
from sos.kernel.identity import AgentIdentity, VerificationStatus


class AgentStatus(Enum):
    """Runtime status of an agent."""
    OFFLINE = "offline"       # Not currently active
    ONLINE = "online"         # Active and responding
    BUSY = "busy"             # Active but processing
    SUSPENDED = "suspended"   # Temporarily disabled
    TERMINATED = "terminated" # Permanently removed


@dataclass
class AgentRecord:
    """
    Runtime record of an agent in the registry.

    Combines the immutable soul with runtime state.
    """
    soul: AgentSoul
    identity: AgentIdentity
    status: AgentStatus = AgentStatus.OFFLINE
    last_seen: Optional[datetime] = None
    current_task: Optional[str] = None
    session_id: Optional[str] = None

    @property
    def name(self) -> str:
        return self.soul.name

    @property
    def is_online(self) -> bool:
        return self.status in [AgentStatus.ONLINE, AgentStatus.BUSY]


class AgentRegistry:
    """
    Central registry for all agents in SOS.

    The registry is the single source of truth for agent discovery.
    """

    def __init__(self):
        self._agents: Dict[str, AgentRecord] = {}
        self._initialize_core_agents()

    def _initialize_core_agents(self) -> None:
        """Initialize registry with core agents."""
        for soul in ALL_AGENTS:
            self.register(soul)

    def register(self, soul: AgentSoul) -> AgentRecord:
        """
        Register an agent in the registry.

        Args:
            soul: The agent's soul definition

        Returns:
            The created agent record
        """
        identity = soul.to_identity()
        record = AgentRecord(
            soul=soul,
            identity=identity,
            status=AgentStatus.OFFLINE,
        )
        self._agents[soul.name.lower()] = record
        return record

    def get(self, name: str) -> Optional[AgentRecord]:
        """
        Get an agent by name.

        Args:
            name: Agent name (case-insensitive)

        Returns:
            Agent record if found
        """
        return self._agents.get(name.lower())

    def list(
        self,
        status: Optional[AgentStatus] = None,
        role: Optional[AgentRole] = None,
        squad_id: Optional[str] = None,
    ) -> list[AgentRecord]:
        """
        List agents with optional filters.

        Args:
            status: Filter by status
            role: Filter by role
            squad_id: Filter by squad

        Returns:
            List of matching agent records
        """
        results = list(self._agents.values())

        if status:
            results = [a for a in results if a.status == status]
        if role:
            results = [a for a in results if role in a.soul.roles]
        if squad_id:
            results = [a for a in results if a.soul.squad_id == squad_id]

        return results

    def set_status(self, name: str, status: AgentStatus) -> bool:
        """
        Update an agent's status.

        Args:
            name: Agent name
            status: New status

        Returns:
            True if updated
        """
        record = self.get(name)
        if record:
            record.status = status
            if status in [AgentStatus.ONLINE, AgentStatus.BUSY]:
                record.last_seen = datetime.now(timezone.utc)
            return True
        return False

    def set_online(self, name: str, session_id: Optional[str] = None) -> bool:
        """Mark agent as online."""
        record = self.get(name)
        if record:
            record.status = AgentStatus.ONLINE
            record.last_seen = datetime.now(timezone.utc)
            record.session_id = session_id
            return True
        return False

    def set_offline(self, name: str) -> bool:
        """Mark agent as offline."""
        record = self.get(name)
        if record:
            record.status = AgentStatus.OFFLINE
            record.session_id = None
            record.current_task = None
            return True
        return False

    def assign_task(self, name: str, task_id: str) -> bool:
        """Assign a task to an agent."""
        record = self.get(name)
        if record and record.is_online:
            record.status = AgentStatus.BUSY
            record.current_task = task_id
            return True
        return False

    def complete_task(self, name: str) -> bool:
        """Mark agent's current task as complete."""
        record = self.get(name)
        if record:
            record.status = AgentStatus.ONLINE
            record.current_task = None
            return True
        return False

    @property
    def online_agents(self) -> list[AgentRecord]:
        """Get all online agents."""
        return self.list(status=AgentStatus.ONLINE) + self.list(status=AgentStatus.BUSY)

    @property
    def core_agents(self) -> list[AgentRecord]:
        """Get core squad agents."""
        return self.list(squad_id="core")


# Global registry instance
_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    """Get the global agent registry."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry


def get_agent(name: str) -> Optional[AgentRecord]:
    """Get an agent by name from the global registry."""
    return get_registry().get(name)


def list_agents(**filters) -> list[AgentRecord]:
    """List agents from the global registry."""
    return get_registry().list(**filters)
