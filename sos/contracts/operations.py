"""
SOS Operations Contract — The Delivery Layer.

An Operation is a self-running delivery pipeline that turns a product purchase
into done work a customer can see. Not a task list — an operational unit.

Operation = Objective + Team (roles) + Phases (sequential) + Deliverables + Budget

Example: Content Writer product → Operation that researches, writes, reviews,
publishes blog posts to the customer's WordPress, and sends a weekly report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class OperationStatus(str, Enum):
    PENDING = "pending"           # Created, not yet started
    ACTIVE = "active"             # Running on schedule
    EXECUTING = "executing"       # Currently in a run cycle
    PAUSED = "paused"             # Manually paused
    DELIVERED = "delivered"       # Current cycle delivered
    FAILED = "failed"             # Current cycle failed
    CANCELLED = "cancelled"       # Permanently stopped


class PhaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"             # Passed the gate
    FAILED = "failed"             # Failed the gate
    SKIPPED = "skipped"


@dataclass
class Role:
    """A role in an operation team. Not a named agent — a capability."""
    name: str                     # "researcher", "writer", "reviewer", "publisher"
    model: str                    # "gemma-4-31b", "gemini-flash", or "tool:wordpress_api"
    task_template: str            # Template with {variables}
    max_tokens: int = 2000
    temperature: float = 0.7


@dataclass
class Gate:
    """Quality gate that must pass before moving to next phase."""
    metric: str                   # "seo_score", "word_count", "readability"
    operator: str                 # ">", "<", ">=", "contains"
    threshold: Any                # 70, 500, "keyword"

    def evaluate(self, value: Any) -> bool:
        ops = {
            ">": lambda v, t: v > t,
            "<": lambda v, t: v < t,
            ">=": lambda v, t: v >= t,
            "<=": lambda v, t: v <= t,
            "==": lambda v, t: v == t,
            "contains": lambda v, t: t in str(v),
        }
        fn = ops.get(self.operator)
        if not fn:
            return True
        try:
            return fn(value, self.threshold)
        except Exception:
            return False


@dataclass
class Phase:
    """A phase in an operation. Phases run sequentially."""
    name: str                     # "research", "draft", "review", "publish", "report"
    roles: list[str]              # Which roles execute this phase
    input_key: str = ""           # Key from previous phase output
    output_key: str = ""          # Key to pass to next phase
    gate: Optional[Gate] = None   # Quality gate (must pass to proceed)
    status: PhaseStatus = PhaseStatus.PENDING
    result: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "roles": self.roles,
            "status": self.status.value,
            "result": self.result,
        }


@dataclass
class Deliverable:
    """What the customer sees."""
    type: str                     # "wordpress_post", "email_report", "social_post"
    config: dict = field(default_factory=dict)  # type-specific config
    verify: str = ""              # How to verify delivery ("http_200", "email_sent")
    frequency: str = "per_run"    # "per_run", "daily", "weekly"


@dataclass
class OperationBudget:
    """Resource budget for an operation."""
    tokens_per_run: int = 5000
    max_retries: int = 2
    cost_per_run_usd: float = 0.0     # $0 for free models
    monthly_cap_usd: float = 50.0


@dataclass
class Operation:
    """
    A self-running delivery pipeline.

    Turns a product purchase into done work.
    """
    id: str                                # "op:stemminds:content-writer"
    customer: str                          # "stemminds"
    product: str                           # "content-writer"
    objective: str                         # "4 blog posts per week about STEM education"

    # Team
    team: list[Role] = field(default_factory=list)

    # Execution
    phases: list[Phase] = field(default_factory=list)
    schedule: str = ""                     # Cron expression

    # Delivery
    deliverables: list[Deliverable] = field(default_factory=list)

    # Budget
    budget: OperationBudget = field(default_factory=OperationBudget)

    # Customer context (from onboarding form)
    context: dict = field(default_factory=dict)  # {wordpress_url, brand_voice, topics, email}

    # State
    status: OperationStatus = OperationStatus.PENDING
    current_phase: str = ""
    run_count: int = 0
    last_run: Optional[str] = None
    last_result: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "customer": self.customer,
            "product": self.product,
            "objective": self.objective,
            "status": self.status.value,
            "schedule": self.schedule,
            "current_phase": self.current_phase,
            "run_count": self.run_count,
            "last_run": self.last_run,
            "phases": [p.to_dict() for p in self.phases],
            "budget": {
                "tokens_per_run": self.budget.tokens_per_run,
                "cost_per_run": self.budget.cost_per_run_usd,
            },
        }
