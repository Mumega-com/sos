#!/usr/bin/env python3
"""
Portfolio Cortex — whole-portfolio perception for Sovereign.

Reads live squad/task state, scores backlog work, checks service health,
and reports current execution capacity without invoking any LLM.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


from kernel.config import MIRROR_URL, SQUAD_URL, SOS_ENGINE_URL

SQUAD_TOKEN = os.environ.get("SOS_SYSTEM_TOKEN", "sk-sos-system")
SQUAD_HEADERS = {"Authorization": f"Bearer {SQUAD_TOKEN}"}
ENGINE_URL = SOS_ENGINE_URL
OPENCLAW_CONFIG = Path("/home/mumega/.openclaw/openclaw.json")
OPENCLAW_AGENTS_DIR = Path("/home/mumega/.openclaw/agents")
REVENUE_PROJECTS = {"dentalnearyou", "gaf", "viamar", "stemminds", "pecb"}
PRIORITY_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class SquadSummary:
    id: str
    name: str
    project: str
    objective: str
    status: str
    total_tasks: int
    task_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class ScoredTask:
    id: str
    squad_id: str
    project: str
    title: str
    priority: str
    status: str
    score: int
    staleness_days: int
    blocks_count: int
    revenue_project: bool
    updated_at: str


@dataclass
class ServiceHealth:
    name: str
    status: str
    detail: str = ""
    latency_ms: int = 0


@dataclass
class AgentCapacity:
    agent_id: str
    tmux_session: bool
    registered: bool
    responsive: bool
    last_session_at: str | None = None
    runtime: str = ""


@dataclass
class CapacitySnapshot:
    tmux_sessions: list[str] = field(default_factory=list)
    openclaw_agents: list[AgentCapacity] = field(default_factory=list)


@dataclass
class PortfolioState:
    squads: list[SquadSummary]
    scored_tasks: list[ScoredTask]
    services: list[ServiceHealth]
    capacity: CapacitySnapshot
    timestamp: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _staleness_days(task: dict[str, Any]) -> int:
    updated = _parse_dt(task.get("updated_at")) or _parse_dt(task.get("created_at"))
    if not updated:
        return 0
    delta = _now() - updated
    return max(int(delta.total_seconds() // 86400), 0)


def _score_task(task: dict[str, Any]) -> ScoredTask:
    priority = str(task.get("priority", "medium")).lower()
    project = str(task.get("project", ""))
    blocks_count = len(task.get("blocks") or [])
    staleness_days = _staleness_days(task)
    revenue_project = project in REVENUE_PROJECTS
    score = (
        PRIORITY_WEIGHTS.get(priority, 1) * 10
        + blocks_count * 5
        + staleness_days * 2
        + (20 if revenue_project else 0)
    )
    return ScoredTask(
        id=str(task.get("id", "")),
        squad_id=str(task.get("squad_id", "")),
        project=project,
        title=str(task.get("title", "")),
        priority=priority,
        status=str(task.get("status", "")),
        score=score,
        staleness_days=staleness_days,
        blocks_count=blocks_count,
        revenue_project=revenue_project,
        updated_at=str(task.get("updated_at", "")),
    )


def _http_health(name: str, url: str) -> ServiceHealth:
    started = _now()
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(url)
        latency_ms = int((_now() - started).total_seconds() * 1000)
        status = "up" if response.status_code == 200 else "down"
        detail = f"http_{response.status_code}"
        return ServiceHealth(name=name, status=status, detail=detail, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = int((_now() - started).total_seconds() * 1000)
        return ServiceHealth(name=name, status="down", detail=str(exc)[:160], latency_ms=latency_ms)


def _openclaw_health() -> ServiceHealth:
    # OpenClaw removed 2026-04-23 — agents now run via tmux + Claude Code directly.
    # Return "removed" so the brain does not generate restart tasks for this service.
    return ServiceHealth(name="openclaw", status="removed", detail="intentionally_removed", latency_ms=0)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _tmux_sessions() -> list[str]:
    try:
        result = subprocess.run(
            ["tmux", "ls"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        sessions: list[str] = []
        for line in result.stdout.splitlines():
            if ":" in line:
                sessions.append(line.split(":", 1)[0].strip())
        return sessions
    except Exception:
        return []


def _agent_session_time(agent_id: str) -> str | None:
    sessions_dir = OPENCLAW_AGENTS_DIR / agent_id / "sessions"
    if not sessions_dir.exists():
        return None
    latest: datetime | None = None
    for path in sessions_dir.glob("*.jsonl"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if latest is None or modified > latest:
            latest = modified
    return latest.isoformat() if latest else None


def _openclaw_agents(tmux_sessions: list[str]) -> list[AgentCapacity]:
    config = _load_json(OPENCLAW_CONFIG)
    configured = config.get("agents", {}).get("list", [])
    capacities: list[AgentCapacity] = []
    for agent in configured:
        agent_id = str(agent.get("id", "")).strip()
        if not agent_id:
            continue
        runtime = "openclaw"
        if agent_id in tmux_sessions:
            runtime = "tmux+openclaw"
        last_session_at = _agent_session_time(agent_id)
        responsive = bool(last_session_at) or (agent_id in tmux_sessions)
        capacities.append(
            AgentCapacity(
                agent_id=agent_id,
                tmux_session=agent_id in tmux_sessions,
                registered=True,
                responsive=responsive,
                last_session_at=last_session_at,
                runtime=runtime,
            )
        )
    capacities.sort(key=lambda item: (not item.responsive, item.agent_id))
    return capacities


def snapshot_portfolio() -> PortfolioState:
    with httpx.Client(timeout=10.0) as client:
        squads_response = client.get(f"{SQUAD_URL}/squads", headers=SQUAD_HEADERS)
        tasks_response = client.get(f"{SQUAD_URL}/tasks", headers=SQUAD_HEADERS)
        squads_response.raise_for_status()
        tasks_response.raise_for_status()
        squads_data = squads_response.json()
        tasks_data = tasks_response.json()

    tasks_by_squad: dict[str, list[dict[str, Any]]] = {}
    for task in tasks_data:
        tasks_by_squad.setdefault(str(task.get("squad_id", "")), []).append(task)

    squad_summaries: list[SquadSummary] = []
    for squad in squads_data:
        squad_id = str(squad.get("id", ""))
        squad_tasks = tasks_by_squad.get(squad_id, [])
        task_counts: dict[str, int] = {}
        for task in squad_tasks:
            status = str(task.get("status", "unknown"))
            task_counts[status] = task_counts.get(status, 0) + 1
        squad_summaries.append(
            SquadSummary(
                id=squad_id,
                name=str(squad.get("name", "")),
                project=str(squad.get("project", "")),
                objective=str(squad.get("objective", "")),
                status=str(squad.get("status", "")),
                total_tasks=len(squad_tasks),
                task_counts=task_counts,
            )
        )

    backlog_tasks = [task for task in tasks_data if str(task.get("status", "")) == "backlog"]
    scored_tasks = sorted(
        (_score_task(task) for task in backlog_tasks),
        key=lambda item: (-item.score, item.priority, item.title.lower()),
    )

    tmux_sessions = _tmux_sessions()
    services = [
        _http_health("mirror", f"{MIRROR_URL}/"),
        _http_health("engine", f"{ENGINE_URL}/health"),
        _http_health("squad", f"{SQUAD_URL}/health"),
        _openclaw_health(),
    ]
    capacity = CapacitySnapshot(
        tmux_sessions=tmux_sessions,
        openclaw_agents=_openclaw_agents(tmux_sessions),
    )

    return PortfolioState(
        squads=squad_summaries,
        scored_tasks=scored_tasks,
        services=services,
        capacity=capacity,
        timestamp=_now().isoformat(),
    )


def render_portfolio_context(state: PortfolioState) -> str:
    lines: list[str] = [f"TIMESTAMP: {state.timestamp}"]

    squad_lines: list[str] = []
    for squad in state.squads:
        counts = ", ".join(f"{status}={count}" for status, count in sorted(squad.task_counts.items()))
        squad_lines.append(
            f"[{squad.status}] {squad.name} ({squad.project}) — total={squad.total_tasks}"
            + (f" | {counts}" if counts else "")
        )
    if squad_lines:
        lines.append("SQUADS:\n" + "\n".join(squad_lines))

    task_lines: list[str] = []
    for task in state.scored_tasks[:10]:
        revenue_flag = " revenue" if task.revenue_project else ""
        task_lines.append(
            f"[{task.score}] [{task.priority}] {task.project}/{task.squad_id} — {task.title}"
            f" | stale={task.staleness_days}d blocks={task.blocks_count}{revenue_flag}"
        )
    if task_lines:
        lines.append("TOP BACKLOG TASKS:\n" + "\n".join(task_lines))

    service_line = " | ".join(f"{service.name}:{service.status}" for service in state.services)
    lines.append("SERVICES: " + service_line)

    capacity_lines: list[str] = []
    for agent in state.capacity.openclaw_agents:
        if agent.tmux_session or agent.responsive:
            last_seen = agent.last_session_at or "never"
            capacity_lines.append(
                f"{agent.agent_id} | tmux={agent.tmux_session} | responsive={agent.responsive} | last_session={last_seen}"
            )
    if capacity_lines:
        lines.append("CAPACITY:\n" + "\n".join(capacity_lines[:12]))

    return "\n\n".join(lines)


def _print_snapshot() -> int:
    state = snapshot_portfolio()
    print(json.dumps(asdict(state), indent=2))
    return 0


def _print_next() -> int:
    state = snapshot_portfolio()
    for index, task in enumerate(state.scored_tasks[:5], start=1):
        print(f"{index}. [{task.score}] {task.project}/{task.squad_id} [{task.priority}] {task.title}")
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "snapshot"
    if command == "snapshot":
        return _print_snapshot()
    if command == "next":
        return _print_next()
    if command == "context":
        print(render_portfolio_context(snapshot_portfolio()))
        return 0
    print("Usage: python3 cortex.py [snapshot|next|context]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
