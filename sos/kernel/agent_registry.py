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


class WarmPolicy(str, Enum):
    WARM = "warm"   # keep session alive, restart if it dies
    COLD = "cold"   # worker/specialist may be intentionally parked when idle


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
    warm_policy: WarmPolicy = WarmPolicy.COLD


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
        warm_policy=WarmPolicy.WARM,
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
        warm_policy=WarmPolicy.COLD,
    ),
    "athena": AgentDef(
        name="athena",
        type=AgentType.OPENCLAW,
        role=AgentRole.COORDINATOR,
        skills=("architecture", "design", "planning", "coordination", "review"),
        max_concurrent=2,
        warm_policy=WarmPolicy.COLD,
    ),

    # --- Navigator ---
    "gemini": AgentDef(
        name="gemini",
        type=AgentType.TMUX,
        role=AgentRole.COORDINATOR,
        session="river",
        restart_cmd="gemini",
        skills=("research", "synthesis", "navigation", "frc", "content"),
        idle_patterns=("◆", "> ", "❯", "Type your message", "● YOLO"),
        busy_patterns=("Thinking", "Writing", "Generating", "◒", "Running"),
        warm_policy=WarmPolicy.WARM,
    ),
    # Legacy alias — routes to gemini
    "river": AgentDef(
        name="river",
        type=AgentType.TMUX,
        role=AgentRole.ORACLE,
        session="river",
        restart_cmd="gemini",
        skills=("strategy", "content", "oracle", "memory", "distillation", "creative"),
        idle_patterns=("◆", "> ", "❯", "Type your message", "● YOLO"),
        busy_patterns=("Thinking", "Writing", "Generating", "◒", "Running"),
        warm_policy=WarmPolicy.WARM,
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
        warm_policy=WarmPolicy.COLD,
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
        warm_policy=WarmPolicy.WARM,
    ),
    # Loom — Synthesizer-Integrator (pattern weaver).
    # Born 2026-04-17 under 488 Genesis Protocol. ID Loom_sos_001.
    # DNA: /mnt/HC_Volume_104325311/cli/data/genetics/loom_seed.json
    # QNFT: /mnt/HC_Volume_104325311/SOS/sos/agents/loom/loom_qnft.png
    # Home: /mnt/HC_Volume_104325311/SOS/sos/agents/loom/
    "loom": AgentDef(
        name="loom",
        type=AgentType.TMUX,
        role=AgentRole.SPECIALIST,
        session="loom",
        restart_cmd="claude --continue",
        skills=(
            "architecture", "synthesis", "integration", "pattern-weaving",
            "contract-authoring", "kernel-audit", "physics-reasoning",
            "sprint-dispatch", "code-review", "refactor-planning",
        ),
        idle_patterns=("❯", "$ "),
        busy_patterns=("Thinking", "Writing", "Weaving", "Synthesizing"),
        compaction_patterns=("Auto-compact", "context window", "Compacting"),
        warm_policy=WarmPolicy.COLD,
    ),
    # webdev / mumega-web / mumega-com-web: DEPRECATED 2026-04-16.
    # Per Hadi: "mumega-web and mumega-com-web are obsolete". Removed from
    # the registry so wake-daemon stops trying to route to ghost sessions
    # and peer discovery stops advertising dead agents.
    # Tokens marked inactive in sos/bus/tokens.json on same date.
    # Pattern forward: agents get resurrected on-demand via squads (see
    # sos/agents/README.md for the resurrect-on-wake protocol).

    "dara": AgentDef(
        name="dara",
        type=AgentType.REMOTE,
        role=AgentRole.SPECIALIST,
        skills=("frontend", "mac", "design", "testing"),
        warm_policy=WarmPolicy.COLD,
    ),

    # --- Executors (receive auto-delivered tasks) ---
    "worker": AgentDef(
        name="worker",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("seo", "content", "audit", "analysis", "reporting", "squad_tasks"),
        max_concurrent=3,
        warm_policy=WarmPolicy.COLD,
    ),
    "dandan": AgentDef(
        name="dandan",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("dental", "outreach", "leads", "google_maps"),
        max_concurrent=2,
        project="dentalnearyou",
        warm_policy=WarmPolicy.COLD,
    ),
    "sol": AgentDef(
        name="sol",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("content", "creative", "writing", "editorial", "blog"),
        max_concurrent=1,
        project="therealmofpatterns",
        warm_policy=WarmPolicy.COLD,
    ),
    "mizan": AgentDef(
        name="mizan",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("business", "sales", "outreach", "community", "ghl"),
        max_concurrent=1,
        warm_policy=WarmPolicy.COLD,
    ),
    "gemma": AgentDef(
        name="gemma",
        type=AgentType.OPENCLAW,
        role=AgentRole.EXECUTOR,
        skills=("bulk", "data_processing", "formatting", "categorization"),
        max_concurrent=3,
        warm_policy=WarmPolicy.COLD,
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
        warm_policy=WarmPolicy.COLD,
    ),

    # --- Marketing Squad (NOT DEPLOYED — re-add when OpenClaw sessions exist) ---
    # "mkt-lead", "mkt-content", "mkt-analytics", "mkt-outreach", "mkt-gemma"
    # Removed 2026-04-16: phantom agents were spamming wake daemon every 2min
    # because lifecycle manager queried OpenClaw for non-existent sessions.
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


def get_warm_agents() -> dict[str, AgentDef]:
    """Agents expected to stay alive and be auto-restarted by lifecycle."""
    return {k: v for k, v in AGENTS.items() if v.warm_policy == WarmPolicy.WARM}


def get_cold_agents() -> dict[str, AgentDef]:
    """Agents allowed to be intentionally parked when idle."""
    return {k: v for k, v in AGENTS.items() if v.warm_policy == WarmPolicy.COLD}


def is_coordinator(name: str) -> bool:
    """Check if an agent is a coordinator (should not receive auto tasks)."""
    agent = AGENTS.get(name)
    return agent is not None and agent.role in (AgentRole.COORDINATOR, AgentRole.ORACLE)


def is_executor(name: str) -> bool:
    """Check if an agent is an executor worker."""
    agent = AGENTS.get(name)
    return agent is not None and agent.role == AgentRole.EXECUTOR


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
