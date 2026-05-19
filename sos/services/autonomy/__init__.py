"""
SOS Autonomy Service
====================

Orchestrates autonomous agent behaviors: dreaming, reflection, avatar generation,
social automation, and metabolism.

Components:
- Dreams: LLM-powered insight synthesis (sos.kernel.dreams)
- Metabolism: Economic cost tracking and heartbeat (sos.kernel.metabolism)
- Avatar: QNFT generation on alpha drift (via sos.clients.identity — HTTP)

Usage:
    from sos.services.autonomy import AutonomyService, AutonomyConfig

    service = AutonomyService(agent_id="agent:River")
    await service.start()
"""

from sos.services.autonomy.service import (
    AutonomyService,
    AutonomyConfig,
)
from sos.services.autonomy.coordinator import AutonomyCoordinator

__all__ = [
    "AutonomyService",
    "AutonomyConfig",
    "AutonomyCoordinator",
]
