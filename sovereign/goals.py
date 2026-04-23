#!/usr/bin/env python3
"""
Sovereign Goal System — The Agentic OS Control Law

Based on the FRC-Agentic formulation:

  Goal: desired coherent future state with measurable markers and constraints
  Objection: structured resistance that blocks trajectory toward goal
  Agent: local optimizer that advances goal by resolving objections while preserving coherence
  OS: multi-agent coherence-governed coordinator

Action Utility:
  U(a) = α·P(a) - β·O(a) + γ·C(a) - δ·R(a)

  P(a) = expected progress toward goal
  O(a) = objection cost triggered or unresolved
  C(a) = coherence preserved or increased
  R(a) = risk introduced

Agent chooses action with highest: progress - resistance + coherence - risk

Storage: Mirror API (:8844) — goals and objections as structured engrams
"""

import json
import time
import uuid
import logging
import requests
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)

from kernel.config import MIRROR_URL, MIRROR_TOKEN

HEADERS = {"Authorization": f"Bearer {MIRROR_TOKEN}", "Content-Type": "application/json"}


# ============================================
# Goal Model — G = (T, M, C*, τ)
# ============================================

class GoalStatus(Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


@dataclass
class Goal:
    """A desired coherent future state with measurable markers and constraints."""
    id: str
    title: str
    target_state: str                    # T — what "done" looks like
    success_markers: List[str]           # M — measurable checkpoints
    coherence_threshold: float = 0.7     # C* — minimum acceptable coherence
    deadline: Optional[str] = None       # τ — time horizon (ISO date)
    priority: str = "medium"             # urgent, high, medium, low
    project: str = "mumega"              # which project this belongs to
    parent_goal_id: Optional[str] = None # for subgoal decomposition
    assigned_agents: List[str] = field(default_factory=list)
    status: str = "active"
    progress: float = 0.0               # 0.0 to 1.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict) -> "Goal":
        return Goal(**{k: v for k, v in d.items() if k in Goal.__dataclass_fields__})


# ============================================
# Objection Model — O = (type, intensity, source, persistence)
# ============================================

class ObjectionType(Enum):
    RESOURCE = "resource"          # not enough money, time, compute
    LOGICAL = "logical"            # plan contradiction, dependency missing
    ENVIRONMENTAL = "environmental" # API down, access denied
    SOCIAL = "social"              # stakeholder disagreement
    COHERENCE = "coherence"        # conflicts with system identity or other goals
    SAFETY = "safety"              # legal, ethical, risk boundary
    PSYCHOLOGICAL = "psychological" # hesitation, ambiguity, drift


class ObjectionPersistence(Enum):
    ACTIVE = "active"        # currently blocking
    MITIGATED = "mitigated"  # reduced but not eliminated
    RESOLVED = "resolved"    # no longer blocking
    RECURRING = "recurring"  # comes back periodically


@dataclass
class Objection:
    """Structured resistance that blocks movement toward a goal."""
    id: str
    goal_id: str
    type: str                           # ObjectionType value
    description: str
    intensity: float = 0.5              # 0.0 (trivial) to 1.0 (showstopper)
    source: str = ""                    # where it comes from
    persistence: str = "active"         # ObjectionPersistence value
    assigned_agent: Optional[str] = None
    resolution_action: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict) -> "Objection":
        return Objection(**{k: v for k, v in d.items() if k in Objection.__dataclass_fields__})


# ============================================
# Agent Policy — what an agent is doing right now
# ============================================

@dataclass
class AgentPolicy:
    """An agent's current operating state relative to its goal."""
    agent: str
    current_goal_id: str
    active_objections: List[str] = field(default_factory=list)
    next_action: str = ""
    expected_progress: float = 0.0      # P(a)
    objection_cost: float = 0.0         # O(a)
    coherence_effect: float = 0.0       # C(a)
    risk: float = 0.0                   # R(a)
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def utility(self) -> float:
        """U(a) = α·P - β·O + γ·C - δ·R"""
        alpha, beta, gamma, delta = 1.0, 0.8, 0.6, 0.5
        return (alpha * self.expected_progress
                - beta * self.objection_cost
                + gamma * self.coherence_effect
                - delta * self.risk)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["utility"] = self.utility
        return d


# ============================================
# Goal Store — Mirror API persistence
# ============================================

class GoalStore:
    """Persist goals and objections to Mirror API as structured engrams."""

    def create_goal(self, goal: Goal) -> str:
        """Store a new goal."""
        try:
            r = requests.post(f"{MIRROR_URL}/store", json={
                "agent": "os",
                "context_id": f"goal_{goal.id}",
                "text": f"GOAL: {goal.title} — {goal.target_state}",
                "epistemic_truths": goal.success_markers,
                "core_concepts": ["goal", goal.project, goal.priority, goal.status],
                "raw_data": {"goal": goal.to_dict()},
            }, headers=HEADERS, timeout=10)
            logger.info(f"Goal created: {goal.id} — {goal.title}")
            return goal.id
        except Exception as e:
            logger.error(f"Failed to store goal: {e}")
            return goal.id

    def create_objection(self, objection: Objection) -> str:
        """Store a new objection."""
        try:
            r = requests.post(f"{MIRROR_URL}/store", json={
                "agent": "os",
                "context_id": f"objection_{objection.id}",
                "text": f"OBJECTION [{objection.type}] on {objection.goal_id}: {objection.description}",
                "epistemic_truths": [f"Blocks goal {objection.goal_id}", f"Intensity: {objection.intensity}"],
                "core_concepts": ["objection", objection.type, objection.persistence],
                "raw_data": {"objection": objection.to_dict()},
            }, headers=HEADERS, timeout=10)
            logger.info(f"Objection created: {objection.id} — {objection.description[:50]}")
            return objection.id
        except Exception as e:
            logger.error(f"Failed to store objection: {e}")
            return objection.id

    def get_goals(self, project: Optional[str] = None, status: str = "active") -> List[Goal]:
        """Retrieve goals from Mirror."""
        try:
            query = f"GOAL status:{status}"
            if project:
                query += f" project:{project}"
            r = requests.post(f"{MIRROR_URL}/search", json={
                "query": query,
                "top_k": 50,
                "agent_filter": "os",
            }, headers=HEADERS, timeout=10)
            results = r.json().get("results", [])
            goals = []
            for result in results:
                raw = result.get("raw_data", {})
                if raw.get("goal"):
                    goals.append(Goal.from_dict(raw["goal"]))
            return goals
        except Exception as e:
            logger.error(f"Failed to get goals: {e}")
            return []

    def get_objections(self, goal_id: str, persistence: str = "active") -> List[Objection]:
        """Retrieve objections for a goal."""
        try:
            r = requests.post(f"{MIRROR_URL}/search", json={
                "query": f"OBJECTION goal:{goal_id} persistence:{persistence}",
                "top_k": 20,
                "agent_filter": "os",
            }, headers=HEADERS, timeout=10)
            results = r.json().get("results", [])
            objections = []
            for result in results:
                raw = result.get("raw_data", {})
                if raw.get("objection"):
                    objections.append(Objection.from_dict(raw["objection"]))
            return objections
        except Exception as e:
            logger.error(f"Failed to get objections: {e}")
            return []

    def update_goal_progress(self, goal_id: str, progress: float, status: str = None):
        """Update goal progress and optionally status."""
        try:
            r = requests.post(f"{MIRROR_URL}/store", json={
                "agent": "os",
                "context_id": f"goal_{goal_id}",
                "text": f"GOAL UPDATE: {goal_id} progress={progress:.0%}" + (f" status={status}" if status else ""),
                "core_concepts": ["goal", "update"],
                "raw_data": {"goal_update": {"id": goal_id, "progress": progress, "status": status}},
            }, headers=HEADERS, timeout=10)
        except Exception as e:
            logger.error(f"Failed to update goal: {e}")

    def resolve_objection(self, objection_id: str, resolution: str):
        """Mark an objection as resolved."""
        try:
            r = requests.post(f"{MIRROR_URL}/store", json={
                "agent": "os",
                "context_id": f"objection_{objection_id}",
                "text": f"OBJECTION RESOLVED: {objection_id} — {resolution}",
                "core_concepts": ["objection", "resolved"],
                "raw_data": {"objection_update": {"id": objection_id, "persistence": "resolved", "resolution": resolution}},
            }, headers=HEADERS, timeout=10)
        except Exception as e:
            logger.error(f"Failed to resolve objection: {e}")


# ============================================
# OS Coordinator — the meta-layer
# ============================================

class SovereignOS:
    """
    The Agentic OS — multi-agent coherence-governed coordinator.

    Responsibilities:
    - Decompose goals into subgoals
    - Detect objections
    - Reroute agents when blocked
    - Maintain global coherence
    - Prevent local optimization from harming the whole
    """

    def __init__(self):
        self.store = GoalStore()

    def set_goal(self, title: str, target_state: str, success_markers: List[str],
                 project: str = "mumega", priority: str = "medium",
                 deadline: str = None, agents: List[str] = None,
                 coherence_threshold: float = 0.7) -> Goal:
        """Set a new goal for the system."""
        goal = Goal(
            id=f"goal_{uuid.uuid4().hex[:8]}",
            title=title,
            target_state=target_state,
            success_markers=success_markers,
            project=project,
            priority=priority,
            deadline=deadline,
            assigned_agents=agents or [],
            coherence_threshold=coherence_threshold,
        )
        self.store.create_goal(goal)
        return goal

    def raise_objection(self, goal_id: str, obj_type: str, description: str,
                        intensity: float = 0.5, source: str = "") -> Objection:
        """Raise an objection against a goal."""
        objection = Objection(
            id=f"obj_{uuid.uuid4().hex[:8]}",
            goal_id=goal_id,
            type=obj_type,
            description=description,
            intensity=intensity,
            source=source,
        )
        self.store.create_objection(objection)
        return objection

    def evaluate_action(self, agent: str, goal_id: str, action: str,
                        expected_progress: float, objection_cost: float = 0.0,
                        coherence_effect: float = 0.0, risk: float = 0.0) -> AgentPolicy:
        """Evaluate a proposed action using the control law."""
        policy = AgentPolicy(
            agent=agent,
            current_goal_id=goal_id,
            next_action=action,
            expected_progress=expected_progress,
            objection_cost=objection_cost,
            coherence_effect=coherence_effect,
            risk=risk,
        )
        logger.info(
            f"Agent {agent} action '{action[:40]}' → "
            f"U={policy.utility:.2f} (P={expected_progress:.2f} O={objection_cost:.2f} "
            f"C={coherence_effect:.2f} R={risk:.2f})"
        )
        return policy

    def get_system_state(self) -> Dict[str, Any]:
        """Get global system state: all active goals + objections."""
        goals = self.store.get_goals(status="active")
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_goals": len(goals),
            "goals": [],
        }
        for goal in goals:
            objections = self.store.get_objections(goal.id)
            active_objections = [o for o in objections if o.persistence == "active"]
            state["goals"].append({
                "id": goal.id,
                "title": goal.title,
                "project": goal.project,
                "progress": goal.progress,
                "priority": goal.priority,
                "deadline": goal.deadline,
                "agents": goal.assigned_agents,
                "objections": len(active_objections),
                "blocked": any(o.intensity > 0.7 for o in active_objections),
            })
        return state


# ============================================
# CLI interface
# ============================================

def main():
    import sys

    os_instance = SovereignOS()

    if len(sys.argv) < 2:
        print("Usage: goals.py [status|set|objection|resolve]")
        return

    cmd = sys.argv[1]

    if cmd == "status":
        state = os_instance.get_system_state()
        print(f"\n{'='*60}")
        print(f"  SOVEREIGN OS — {state['timestamp'][:19]}")
        print(f"  Active Goals: {state['active_goals']}")
        print(f"{'='*60}")
        for g in state["goals"]:
            blocked = " ⛔ BLOCKED" if g["blocked"] else ""
            print(f"  [{g['priority'][:1].upper()}] {g['title'][:45]}")
            print(f"      Project: {g['project']} | Progress: {g['progress']:.0%} | Objections: {g['objections']}{blocked}")
            if g["agents"]:
                print(f"      Agents: {', '.join(g['agents'])}")
            if g["deadline"]:
                print(f"      Deadline: {g['deadline']}")
        print()

    elif cmd == "set":
        # Quick goal creation from CLI
        if len(sys.argv) < 4:
            print("Usage: goals.py set <project> <title> [deadline]")
            return
        project = sys.argv[2]
        title = sys.argv[3]
        deadline = sys.argv[4] if len(sys.argv) > 4 else None
        goal = os_instance.set_goal(
            title=title,
            target_state=title,
            success_markers=[],
            project=project,
            deadline=deadline,
        )
        print(f"Goal created: {goal.id} — {goal.title}")

    elif cmd == "objection":
        if len(sys.argv) < 5:
            print("Usage: goals.py objection <goal_id> <type> <description>")
            return
        obj = os_instance.raise_objection(
            goal_id=sys.argv[2],
            obj_type=sys.argv[3],
            description=" ".join(sys.argv[4:]),
        )
        print(f"Objection raised: {obj.id} — {obj.description}")


if __name__ == "__main__":
    main()
