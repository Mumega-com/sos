"""Conductance matrix — shared kernel state (FRC 531).

G[agent][skill] is a weighted graph of proven flow between agents and
skills, updated after bounty payouts (``conductance_update``) and decayed
each flywheel cycle (``conductance_decay``).

Lives in the kernel (not a service) because three callers touch it:
  - sos.services.health.calcifer — decay + score task assignments
  - sos.services.feedback.loop   — periodic decay
  - sos.services.journeys.tracker — read conductance for routing

Keeping it in ``sos.services.health`` forced cross-service imports and
broke the R1 independence contract. Moving it here — with pure file I/O
and no service dependencies — makes conductance a first-class kernel
primitive that any service may read/write.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("sos.kernel.conductance")

# dG/dt = |F|^γ - αG
# G_ij = conductance between agent i and skill j
# F_ij = $MIND flow through that agent-skill pair
# γ = reinforcement exponent, α = decay rate

CONDUCTANCE_FILE = Path.home() / ".sos" / "state" / "conductance.json"
CONDUCTANCE_GAMMA = 1.0   # Reinforcement exponent
CONDUCTANCE_ALPHA = 0.01  # Decay rate per cycle


def _load_conductance() -> dict[str, dict[str, float]]:
    """Load the conductance matrix G[agent][skill] from disk."""
    if CONDUCTANCE_FILE.exists():
        try:
            return json.loads(CONDUCTANCE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_conductance(G: dict[str, dict[str, float]]) -> None:
    """Persist the conductance matrix to disk."""
    CONDUCTANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONDUCTANCE_FILE.write_text(json.dumps(G, indent=2))


def conductance_update(agent_id: str, skill: str, reward: float) -> None:
    """After bounty payout, reinforce the agent-skill conductance.

    Called by Wire 4 (task completion) or externally after payout.
    G[agent][skill] += |reward|^γ
    """
    G = _load_conductance()
    if agent_id not in G:
        G[agent_id] = {}
    current = G[agent_id].get(skill, 0.0)
    G[agent_id][skill] = current + abs(reward) ** CONDUCTANCE_GAMMA
    _save_conductance(G)
    logger.debug(f"Conductance: {agent_id}/{skill} = {G[agent_id][skill]:.2f} (+{reward:.0f})")


def conductance_decay() -> None:
    """Decay all conductance values. Called every flywheel cycle (weekly).

    G[agent][skill] *= (1 - α)
    """
    G = _load_conductance()
    if not G:
        return
    for agent_id in G:
        for skill in G[agent_id]:
            G[agent_id][skill] *= (1 - CONDUCTANCE_ALPHA)
    _save_conductance(G)
    logger.info(f"Conductance decay applied (α={CONDUCTANCE_ALPHA})")
