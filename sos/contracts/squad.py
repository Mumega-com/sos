"""
Squad Service Contracts — the interface layer.

These dataclasses define what crosses service boundaries.
No business logic here — just shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


# ── Squad ─────────────────────────────────────────────────────────────────────

class SquadTier(str, Enum):
    NOMAD = "nomad"          # Fast, optimistic, pay-as-you-go
    FORTRESS = "fortress"    # Controlled, pessimistic, retainer
    CONSTRUCT = "construct"  # Autonomous, resonant, guild treasury


class SquadStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


@dataclass
class SquadRole:
    name: str                       # e.g. "analyst", "optimizer", "writer"
    skills: list[str] = field(default_factory=list)
    schedule: str = ""              # e.g. "daily 9am", "weekly Monday"
    description: str = ""
    fuel_grade: str = "diesel"      # diesel|regular|premium|aviation


@dataclass
class SquadMember:
    agent_id: str
    role: str                       # matches SquadRole.name
    joined_at: str = ""
    is_human: bool = False          # for co-ops / human-in-the-loop


@dataclass
class Squad:
    id: str
    name: str
    project: str
    objective: str
    tier: SquadTier = SquadTier.NOMAD
    status: SquadStatus = SquadStatus.DRAFT
    roles: list[SquadRole] = field(default_factory=list)
    members: list[SquadMember] = field(default_factory=list)
    kpis: list[str] = field(default_factory=list)
    budget_cents_monthly: int = 0
    created_at: str = ""
    updated_at: str = ""


# ── Task ──────────────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    QUEUED = "queued"           # approved, waiting for capacity
    CLAIMED = "claimed"         # agent claimed, executing
    IN_PROGRESS = "in_progress"
    REVIEW = "review"           # waiting for human/agent review
    DONE = "done"
    BLOCKED = "blocked"
    CANCELED = "canceled"
    FAILED = "failed"


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class SquadTask:
    id: str
    squad_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.BACKLOG
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee: Optional[str] = None  # agent_id or human
    skill_id: Optional[str] = None  # matched skill
    project: str = ""
    labels: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    token_budget: int = 0
    bounty: dict[str, Any] = field(default_factory=dict)
    external_ref: Optional[str] = None  # ClickUp/Notion/Linear ID
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    claimed_at: Optional[str] = None
    attempt: int = 0            # for idempotent retries


@dataclass
class TaskClaim:
    task_id: str
    assignee: str
    claimed_at: str
    attempt: int


@dataclass
class RoutingDecision:
    task_id: str
    skill_id: Optional[str]
    assignee: Optional[str]
    reason: str
    score: float = 0.0


# ── Skill ─────────────────────────────────────────────────────────────────────

class SkillStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class TrustTier(int, Enum):
    UNVETTED = 1       # Instructions only, no execution
    VERIFIED = 2       # Static analysis passed
    CERTIFIED = 3      # Behavioral sandbox cleared
    VENDOR = 4         # Full vetting + continuous monitoring


class LoadingLevel(int, Enum):
    METADATA = 1       # ~100 tokens, always loaded (name + description)
    INSTRUCTIONS = 2   # <5k tokens, loaded on trigger (full SKILL.md)
    RESOURCES = 3      # Scripts/refs, executed on demand (output only enters context)


@dataclass
class SkillDescriptor:
    """What the skill registry knows about a skill.

    Follows the industry standard: MCP tool schema + Anthropic SKILL.md + progressive disclosure.
    """
    id: str
    name: str
    description: str                                        # what it does + when to use it
    input_schema: dict[str, Any] = field(default_factory=dict)   # JSON Schema for inputs
    output_schema: dict[str, Any] = field(default_factory=dict)  # JSON Schema for outputs
    labels: list[str] = field(default_factory=list)         # for matching (primary)
    keywords: list[str] = field(default_factory=list)       # fallback matching
    entrypoint: str = ""                                    # module:function
    skill_dir: str = ""                                     # path to SKILL.md directory
    required_inputs: list[str] = field(default_factory=list)
    status: SkillStatus = SkillStatus.ACTIVE
    trust_tier: TrustTier = TrustTier.VENDOR
    loading_level: LoadingLevel = LoadingLevel.INSTRUCTIONS
    fuel_grade: str = "diesel"                              # minimum model tier needed
    version: str = "1.0.0"
    deprecated_at: Optional[str] = None                     # ISO date, T-90 deprecation


@dataclass
class SkillMatch:
    skill_id: str
    skill_name: str
    confidence: float           # 0-1, how well the task matches
    match_reason: str           # "label:seo" or "keyword:audit"


@dataclass
class SkillExecutionResult:
    task_id: str
    skill_id: str
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error: str = ""
    tokens_used: int = 0
    duration_ms: int = 0
    attempt: int = 0


# ── State ─────────────────────────────────────────────────────────────────────

@dataclass
class SquadState:
    """Shared working state for a squad on a project."""
    squad_id: str
    project: str
    data: dict[str, Any] = field(default_factory=dict)
    version: int = 0
    updated_at: str = ""


@dataclass
class SquadEvent:
    """Immutable event in squad history."""
    squad_id: str
    event_type: str             # squad.created, task.completed, skill.executed, etc.
    actor: str                  # who triggered it
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


# ── Connector ─────────────────────────────────────────────────────────────────

class ConnectorType(str, Enum):
    CLICKUP = "clickup"
    NOTION = "notion"
    LINEAR = "linear"
    GITHUB = "github"
    MIRROR = "mirror"           # our internal system


@dataclass
class ExternalRef:
    connector: ConnectorType
    external_id: str
    url: str = ""
    synced_at: str = ""


@dataclass
class SyncReport:
    connector: ConnectorType
    imported: int = 0
    exported: int = 0
    errors: list[str] = field(default_factory=list)
    synced_at: str = ""


# ── Pipeline ──────────────────────────────────────────────────────────────────

@dataclass
class PipelineSpec:
    squad_id: str
    repo: str                          # "servathadi/dnu"
    workdir: str = "."                 # subdirectory, e.g. "web"
    default_branch: str = "main"
    feature_branch_prefix: str = "squad/"
    pr_mode: str = "branch_pr"         # "branch_pr" | "direct_main"
    build_cmd: str = ""
    test_cmd: str = ""
    deploy_cmd: str = ""
    smoke_cmd: str = ""
    deploy_mode: str = "manual"        # "auto_on_main" | "manual"
    deploy_on_task_labels: list[str] = field(default_factory=lambda: ["deploy"])
    rollback_cmd: str = ""
    enabled: bool = True


@dataclass
class PipelineRun:
    id: str
    squad_id: str
    task_id: str
    status: str = "pending"            # pending|building|testing|awaiting_approval|deploying|smoke|succeeded|failed|rolled_back
    commit_sha: str = ""
    branch: str = ""
    pr_url: str = ""
    logs: str = ""
    error: str = ""
    created_at: str = ""
    completed_at: str = ""


# ── Bus Events (stable types for squad channel) ──────────────────────────────

SQUAD_EVENTS = {
    "squad.created",
    "squad.activated",
    "squad.paused",
    "squad.archived",
    "task.created",
    "task.claimed",
    "task.routed",
    "task.completed",
    "task.failed",
    "task.blocked",
    "task.review_requested",
    "skill.registered",
    "skill.executed",
    "budget.warning",
    "budget.exhausted",
    "connector.synced",
    "pipeline.started",
    "pipeline.succeeded",
    "pipeline.failed",
    "pipeline.approval_needed",
}
