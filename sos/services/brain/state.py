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
class RoutingOutcome:
    """Observable result of one adaptive-routing decision or suppression."""

    outcome_type: str
    task_id: str
    project: str
    agent_name: str
    status: str
    reason: str
    recorded_at: str


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

    recent_routing_outcomes: list[RoutingOutcome] = field(default_factory=list)
    """Last 50 routing outcomes for slime-mold feedback."""

    routing_outcomes_by_type: dict[str, int] = field(default_factory=dict)
    """Per-outcome counters, e.g. {"handoff_success": 3}."""

    priority_queue: list[tuple[float, int, str]] = field(default_factory=list)
    """Min-heap of (-score, tiebreaker, task_id). Highest score pops first; FIFO on ties."""

    task_skills: dict[str, list[str]] = field(default_factory=dict)
    """Maps task_id → required skills, populated at task.created time.

    Consumed by BrainService._try_dispatch_next to match tasks against the
    skill capabilities of registered agents. Default is an empty list when
    the task.created payload has neither a ``skill_id`` nor ``labels``.
    """

    task_projects: dict[str, str] = field(default_factory=dict)
    """Maps task_id -> project for later routing outcome attribution."""

    assignments_by_agent: dict[str, set[str]] = field(default_factory=dict)
    """Maps agent_name -> set of currently assigned task_ids. Powers agent_load()."""

    failure_counts_by_agent: dict[str, int] = field(default_factory=dict)
    """Recent failure count per agent. Decays on each successful handoff_success."""

    task_retry_counts: dict[str, int] = field(default_factory=dict)
    """How many times a task has been re-queued after failure or dead_agent_skip."""

    _MAX_ROUTING_DECISIONS: int = field(default=50, init=False, repr=False)
    _queue_counter: int = field(default=0, init=False, repr=False)

    # FSD escalation threshold — tasks exceeding this retry count get escalated to human.
    ESCALATION_THRESHOLD: int = field(default=3, init=False, repr=False)
    # Failure penalty applied per failure to agent effective load in selection.
    FAILURE_PENALTY_PER_MISS: int = field(default=3, init=False, repr=False)

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

    def add_routing_outcome(self, outcome: RoutingOutcome) -> None:
        """Append/cap a routing outcome and increment counters."""
        self.recent_routing_outcomes.append(outcome)
        if len(self.recent_routing_outcomes) > self._MAX_ROUTING_DECISIONS:
            self.recent_routing_outcomes = self.recent_routing_outcomes[-self._MAX_ROUTING_DECISIONS:]
        self.routing_outcomes_by_type[outcome.outcome_type] = (
            self.routing_outcomes_by_type.get(outcome.outcome_type, 0) + 1
        )

    def assign_task(self, agent_name: str, task_id: str) -> None:
        """Record that agent_name is now handling task_id."""
        self.assignments_by_agent.setdefault(agent_name, set()).add(task_id)

    def unassign_task(self, agent_name: str, task_id: str) -> None:
        """Remove task from agent's assignment set on completion or failure."""
        self.assignments_by_agent.get(agent_name, set()).discard(task_id)

    def record_agent_failure(self, agent_name: str) -> None:
        """Increment failure count for agent. Used by select_agent penalty."""
        if agent_name:
            self.failure_counts_by_agent[agent_name] = (
                self.failure_counts_by_agent.get(agent_name, 0) + 1
            )

    def record_agent_success(self, agent_name: str) -> None:
        """Decay failure count by 1 on success — agents recover over time."""
        if agent_name and agent_name in self.failure_counts_by_agent:
            self.failure_counts_by_agent[agent_name] = max(
                0, self.failure_counts_by_agent[agent_name] - 1
            )

    def increment_task_retry(self, task_id: str) -> int:
        """Bump retry count for task_id and return the new count."""
        count = self.task_retry_counts.get(task_id, 0) + 1
        self.task_retry_counts[task_id] = count
        return count

    def clear_task_retry(self, task_id: str) -> None:
        self.task_retry_counts.pop(task_id, None)

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
