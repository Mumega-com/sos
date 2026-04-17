"""Brain — autonomous prioritization and dispatch engine.

Specified in docs/docs/architecture/brain.md. Implementation lands here.

The Brain is event-driven, not cron-based. It wakes on meaningful events
(task.created / task.done / agent.woke), scores the full portfolio of open
work across projects, picks the highest-value task, picks the best available
agent, and emits a task.routed event on the bus.

Public API:
    - run_service() — the asyncio entry point (called by __main__.py)
    - score_task(task) -> float — the scoring formula
    - score_task_constrained(task, state) -> Optional[float] — scoring + FRC
      constraint (returns None if dispatch would violate dS + k*d(ln C) = 0)
    - select_agent(task, candidates) -> Optional[AgentIdentity]
    - dispatch(task, agent) -> RoutingDecision

Integration points:
    - Bus: subscribes to `sos:stream:*` for task/agent events
    - Registry: reads AgentIdentity via sos.services.registry
    - ProviderMatrix: calls select_provider() to pick runtime backend
    - Physics: uses sos.kernel.physics.CoherencePhysics for the FRC constraint
    - Squad Service: reads task state via HTTP at :8060 (for now; bus-driven
      reads become primary as the pipeline matures)

Not a god-service. The Brain's one job is deciding who does what next.
Everything else is scored, matched, and delegated.
"""
from __future__ import annotations

from sos.services.brain.service import BrainService
from sos.services.brain.state import BrainState
from sos.services.brain.scoring import score_task, URGENCY_WEIGHTS

__all__ = [
    "BrainService",
    "BrainState",
    "score_task",
    "URGENCY_WEIGHTS",
    "__version__",
]

__version__ = "0.4.3.dev0"
