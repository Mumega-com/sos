"""
SOS Vertex AI ADK Adapter.

Exposes SOS agents as ADK-compatible agents for Google's ecosystem.
"""

from sos.adapters.vertex_adk.agent import (
    SOSAgent,
    SOSAgentADK,
    Soul,
    AgentResponse,
    MemoryProvider,
    MirrorMemoryProvider,
    create_sos_agent,
    BUILTIN_SOULS,
)

__all__ = [
    "SOSAgent",
    "SOSAgentADK",
    "Soul",
    "AgentResponse",
    "MemoryProvider",
    "MirrorMemoryProvider",
    "create_sos_agent",
    "BUILTIN_SOULS",
]
