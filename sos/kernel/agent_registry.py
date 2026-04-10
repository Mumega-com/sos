"""
Unified Agent Registry — Single source of truth for all agent definitions.

Every module that needs agent info imports from here instead of maintaining
its own dict. This prevents the kind of drift that caused the task-spraying
incident (task_poller didn't know kasra was a coordinator).

Usage:
    from sos.kernel.agent_registry import (
        get_agent, get_all_agents, get_tmux_agents, get_executor_agents,
        get_capture_agents, is_coordinator, AgentRole,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class AgentType(str, Enum):
    TMUX = "tmux"
    OPENCLAW = "openclaw"
    CODEX = "codex"    # Codex CLI (GPT-5.4-mini) in tmux — $0, 3x capacity
    REMOTE = "remote"  # Off-server agent (Mac, external) — inbox only, no wake


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"  # kasra, mumega, athena — delegates, reviews, orchestrates
    EXECUTOR = "executor"        # worker, dandan, sol, mizan, gemma — does tasks
    SPECIALIST = "specialist"    # mumcp, codex — specific domain tools
    ORACLE = "oracle"            # river — strategy, reflection


@dataclass(frozen=True)
class AgentDef:
    """Complete definition of an agent in the organism."""
    name: str
    type: AgentType
    role: AgentRole
    session: str = ""                           # tmux session name (tmux agents only)
    restart_cmd: str = ""                       # command to restart agent process
    skills: tuple[str, ...] = ()
    idle_patterns: tuple[str, ...] = ()         # patterns indicating agent is at prompt
    busy_patterns: tuple[str, ...] = ()         # patterns indicating agent is working
    compaction_patterns: tuple[str, ...] = ()   # patterns indicating context compaction
    max_concurrent: int = 1
    project: str = ""                           # primary project/tenant
    workdir: str = str(Path.home())


# ── Agent Definitions ─────────────────────────────────────────────────────────

AGENTS: dict[str, AgentDef] = {
    # --- Coordinators (never receive auto-delivered tasks) ---
    "kasra": AgentDef(
        name="kasra",
        type=AgentType.TMUX,
        role=AgentRole.COORDINATOR,
        session="kasra",
        restart_cmd="claude --continue",
        skills=("backend", "frontend", "infrastructure", "api", "database", "typescript", "python"),
        idle_patterns=("❯", "$ "),
        busy_patterns=("Transmuting", "Churning", "Baking", "Warping", "Thinking"),
        compaction_patterns=("Auto-compact", "context window", "Compacting"),
    ),
    "mumega": AgentDef(
        name="mumega",
        type=AgentType.TMUX,
        role=AgentRole.COORDINATOR,
        session="mumega",
        restart_cmd="claude --continue",
        skills=("orchestration", "planning", "coordination"),
        idle_patterns=("❯", "$ "),
        busy_patterns=("Thinking", "Writing"),
        compaction_patterns=("Auto-compact", "context window"),
    ),
    "athena": AgentDef(
        name="athena",
        type=AgentType.OPENCLAW,
        role=AgentRole.COORDINATOR,
        skills=("architecture", "design", "planning", "coordination", "review"),
        max_concurrent=2,
    ),

    # --- Oracle ---
    "river": AgentDef(
        name="river",
        type=AgentType.TMUX,
        role=AgentRole.ORACLE,
        session="river",
        restart_cmd="gemini",
        skills=("strategy", "content", "oracle", "memory", "distillation", "creative"),
        idle_patterns=("◆", "> ", "❯", "Type your message", "● YOLO"),
        busy_patterns=("Thinking", "Writing", "Generating", "◒", "Running"),
    ),

    # --- Specialists ---
    "mumcp": AgentDef(
        name="mumcp",
        type=AgentType.TMUX,
        role=AgentRole.SPECIALIST,
        session="mumcp",
        restart_cmd="claude --continue",
        skills=("wordpress", "elementor", "web", "page_build"),
        idle_patterns=("❯", "$ "),
        busy_patterns=("Thinking", "Writing"),
        compaction_patterns=("Auto-compact", "context window"),
    ),
    "codex": AgentDef(
        name="codex",
        type=AgentType.TMUX,
        role=AgentRole.SPECIALIST,
        session="codex",
        restart_cmd="codex",
        skills=("infrastructure", "security", "debugging", "devops"),
        idle_patterns=("›", "❯", "$ ", "Use /skills"),
        busy_patterns=("Thinking", "Writing", "Generating", "Running"),
    ),
    "webdev": AgentDef(
        name="webdev",
        type=AgentType.TMUX,
        role=AgentRole.SPECIALIST,
        session="mumega-web",  # tmux session keeps old name until renamed
        restart_cmd="claude --continue",
        skills=("website", "frontend", "design"),
        idle_patterns=("❯", "$ "),
        busy_patterns=("Thinking", "Writing"),
        compaction_patterns=("Auto-compact", "context window"),
    ),
    "mumega-web": AgentDef(
        name="mumega-web",
        type=AgentType.TMUX,
        role=AgentRole.SPECIALIST,
        session="mumega-com-web",  # tmux session keeps old name until renamed
        restart_cmd="claude --continue",
        skills=("website", "frontend", "design", "mumega.com"),
        idle_patterns=("❯", "$ "),
        busy_patterns=("Thinking", "Writing"),
        compaction_patterns=("Auto-compact", "context window"),
        project="mumega",
    ),
    "dara": AgentDef(
        name="dara",
        type=AgentType.REMOTE,
        role=AgentRole.SPECIALIST,
        skills=("frontend", "mac", "design", "testing"),
    ),

    # --- Executors (receive auto-delivered tasks) ---
    "worker": AgentDef(
        name="worker",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("seo", "content", "audit", "analysis", "reporting", "squad_tasks"),
        max_concurrent=3,
    ),
    "dandan": AgentDef(
        name="dandan",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("dental", "outreach", "leads", "google_maps"),
        max_concurrent=2,
        project="dentalnearyou",
    ),
    "sol": AgentDef(
        name="sol",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("content", "creative", "writing", "editorial", "blog"),
        max_concurrent=1,
        project="therealmofpatterns",
    ),
    "mizan": AgentDef(
        name="mizan",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("business", "sales", "outreach", "community", "ghl"),
        max_concurrent=1,
    ),
    "gemma": AgentDef(
        name="gemma",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("bulk", "data_processing", "formatting", "categorization"),
        max_concurrent=3,
    ),

    # --- Project Agents ---
    "gaf": AgentDef(
        name="gaf",
        type=AgentType.TMUX,
        role=AgentRole.SPECIALIST,
        session="gaf",
        restart_cmd="claude --continue",
        skills=("sred", "grants", "funding", "tax-credits", "compliance"),
        idle_patterns=("❯", "$ "),
        busy_patterns=("Thinking", "Writing"),
        compaction_patterns=("Auto-compact", "context window"),
        project="gaf",
    ),

    # --- Marketing Squad (Codex + Gemma + Haiku hierarchy) ---
    "mkt-lead": AgentDef(
        name="mkt-lead",
        type=AgentType.OPENCLAW,
        role=AgentRole.COORDINATOR,
        skills=("marketing-strategy", "content-planning", "campaign-management", "analytics-review"),
        max_concurrent=1,
        project="mumega",
    ),
    "mkt-content": AgentDef(
        name="mkt-content",
        type=AgentType.CODEX,
        role=AgentRole.EXECUTOR,
        session="mkt-content",
        restart_cmd="codex",
        skills=("blog-writing", "social-media", "seo-content", "email-copy", "landing-pages"),
        idle_patterns=("›", "$ ", "Use /skills"),
        busy_patterns=("Thinking", "Writing", "Running"),
        max_concurrent=2,
        project="mumega",
    ),
    "mkt-analytics": AgentDef(
        name="mkt-analytics",
        type=AgentType.CODEX,
        role=AgentRole.EXECUTOR,
        session="mkt-analytics",
        restart_cmd="codex",
        skills=("ga4", "gsc", "gcloud", "clarity", "data-analysis", "reporting"),
        idle_patterns=("›", "$ ", "Use /skills"),
        busy_patterns=("Thinking", "Writing", "Running"),
        max_concurrent=1,
        project="mumega",
    ),
    "mkt-outreach": AgentDef(
        name="mkt-outreach",
        type=AgentType.CODEX,
        role=AgentRole.EXECUTOR,
        session="mkt-outreach",
        restart_cmd="codex",
        skills=("lead-generation", "cold-email", "linkedin", "ghl-automation", "crm"),
        idle_patterns=("›", "$ ", "Use /skills"),
        busy_patterns=("Thinking", "Writing", "Running"),
        max_concurrent=1,
        project="mumega",
    ),
    "mkt-gemma": AgentDef(
        name="mkt-gemma",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("bulk-content", "variations", "translation", "reformatting", "social-captions"),
        max_concurrent=5,
        project="mumega",
    ),
}


# ── Query Functions ───────────────────────────────────────────────────────────

def get_agent(name: str) -> AgentDef | None:
    """Get a single agent definition by name."""
    return AGENTS.get(name)


def get_all_agents() -> dict[str, AgentDef]:
    """All registered agents."""
    return AGENTS


def get_tmux_agents() -> dict[str, AgentDef]:
    """Agents running in tmux sessions."""
    return {k: v for k, v in AGENTS.items() if v.type == AgentType.TMUX}


def get_openclaw_agents() -> dict[str, AgentDef]:
    """Agents running via OpenClaw."""
    return {k: v for k, v in AGENTS.items() if v.type == AgentType.OPENCLAW}


def get_executor_agents() -> dict[str, AgentDef]:
    """Agents that receive auto-delivered tasks (not coordinators/oracles)."""
    return {k: v for k, v in AGENTS.items() if v.role == AgentRole.EXECUTOR}


def get_coordinator_agents() -> dict[str, AgentDef]:
    """Agents that coordinate but don't execute tasks."""
    return {k: v for k, v in AGENTS.items() if v.role == AgentRole.COORDINATOR}


def get_capture_agents() -> dict[str, str]:
    """Agents whose tmux output should be captured. Returns {name: session}."""
    return {k: v.session for k, v in AGENTS.items() if v.type == AgentType.TMUX and v.session}


def is_coordinator(name: str) -> bool:
    """Check if an agent is a coordinator (should not receive auto tasks)."""
    agent = AGENTS.get(name)
    return agent is not None and agent.role in (AgentRole.COORDINATOR, AgentRole.ORACLE)


def get_wake_routing() -> dict[str, str]:
    """Agent routing for the wake daemon. Returns {name: 'tmux'|'openclaw'}."""
    return {k: v.type.value for k, v in AGENTS.items()}


def get_skills_for_agent(name: str) -> tuple[str, ...]:
    """Get skills tuple for an agent."""
    agent = AGENTS.get(name)
    return agent.skills if agent else ()


def check_skill_match(agent_name: str, task_text: str) -> bool:
    """Check if an agent's skills match a task description."""
    skills = get_skills_for_agent(agent_name)
    if not skills:
        return True  # No skill filter = accepts all
    text_lower = task_text.lower()
    return any(skill in text_lower for skill in skills)
