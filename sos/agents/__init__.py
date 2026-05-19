"""
SOS Agents - Agent definitions and registry.

This module contains:
- Agent definitions (the "souls" of each agent)
- Agent registry for discovery
- Onboarding helpers
"""

from sos.agents.registry import AgentRegistry, get_agent, list_agents
from sos.agents.definitions import (
    RIVER,
    KASRA,
    MIZAN,
    MUMEGA,
    CODEX,
    CONSULTANT,
    DANDAN,
    ALL_AGENTS,
)

__all__ = [
    "AgentRegistry",
    "get_agent",
    "list_agents",
    "RIVER",
    "KASRA",
    "MIZAN",
    "MUMEGA",
    "CODEX",
    "CONSULTANT",
    "DANDAN",
    "ALL_AGENTS",
]
