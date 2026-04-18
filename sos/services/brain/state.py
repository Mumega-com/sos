"""Brain in-memory state — observable by the /sos/brain dashboard (Sprint 4)."""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RoutingDecision:
    """A record of one task-routing event (populated in Sprint 3)."""
    task_id: str
    agent_name: str
    score: float
    routed_at: str  # ISO timestamp


@dataclass
class BrainState:
    """Live in-memory state mutated by BrainService event handlers.

    Designed to be read from the dashboard endpoint without locking —
    individual field mutations are effectively atomic in CPython.
    """

    events_seen: int = 0
    """Total events processed (regardless of type)."""

    events_by_type: dict[str, int] = field(default_factory=dict)
    """Per-message-type counters, e.g. {"task.created": 12, "agent_joined": 3}."""

    last_event_at: Optional[str] = None
    """ISO-8601 timestamp of the most recently processed event."""

    tasks_in_flight: set[str] = field(default_factory=set)
    """task_ids that have been created but not yet completed or failed."""

    recent_routing_decisions: list[RoutingDecision] = field(default_factory=list)
    """Last 50 routing decisions. Capped — older decisions are dropped."""

    priority_queue: list[tuple[float, int, str]] = field(default_factory=list)
    """Min-heap of (-score, tiebreaker, task_id). Highest score pops first; FIFO on ties."""

    task_skills: dict[str, list[str]] = field(default_factory=dict)
    """Maps task_id → required skills, populated at task.created time.

    Consumed by BrainService._try_dispatch_next to match tasks against the
    skill capabilities of registered agents. Default is an empty list when
    the task.created payload has neither a ``skill_id`` nor ``labels``.
    """

    _MAX_ROUTING_DECISIONS: int = field(default=50, init=False, repr=False)
    _queue_counter: int = field(default=0, init=False, repr=False)

    def record_event(self, event_type: str, at: str) -> None:
        """Increment counters and update timestamp."""
        self.events_seen += 1
        self.events_by_type[event_type] = self.events_by_type.get(event_type, 0) + 1
        self.last_event_at = at

    def add_routing_decision(self, decision: RoutingDecision) -> None:
        """Append and cap at _MAX_ROUTING_DECISIONS."""
        self.recent_routing_decisions.append(decision)
        if len(self.recent_routing_decisions) > self._MAX_ROUTING_DECISIONS:
            self.recent_routing_decisions = self.recent_routing_decisions[-self._MAX_ROUTING_DECISIONS:]

    def enqueue(self, task_id: str, score: float) -> None:
        """Push a task onto the priority queue.

        Highest score pops first; insertion order breaks ties.
        """
        heapq.heappush(self.priority_queue, (-score, self._queue_counter, task_id))
        self._queue_counter += 1

    def pop_highest(self) -> tuple[str, float] | None:
        """Pop the highest-score task, returning (task_id, score), or None if empty."""
        if not self.priority_queue:
            return None
        neg_score, _tiebreaker, task_id = heapq.heappop(self.priority_queue)
        return task_id, -neg_score

    def queue_size(self) -> int:
        """Return the number of tasks currently queued."""
        return len(self.priority_queue)
