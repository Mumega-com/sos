"""Status-domain MCP tools.

This module is intentionally transport-agnostic. The SSE transport passes in
Redis, tenant scope, stream-prefix behavior, and Squad service credentials.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import requests

KNOWN_AGENTS: dict[str, dict[str, str]] = {
    "kasra": {"type": "tmux", "model": "Claude Opus/Sonnet", "role": "Builder"},
    "loom": {
        "type": "tmux",
        "model": "Claude Opus 4.7",
        "role": (
            "SOS Protocol Custodian — bus, MCP, sessions, tokens, memory scoping, "
            "minting authority (v1)"
        ),
    },
    "mumega": {"type": "tmux", "model": "Claude Opus", "role": "Orchestrator"},
    "codex": {"type": "tmux", "model": "GPT-5.4", "role": "Infra + Security"},
    "mumcp": {"type": "tmux", "model": "Claude Sonnet", "role": "MumCP — WordPress + Elementor"},
    "mumega-web": {"type": "tmux", "model": "Claude Sonnet", "role": "Website"},
    "athena": {"type": "tmux", "model": "Claude Sonnet", "role": "Architecture Review"},
    "sol": {"type": "openclaw", "model": "Claude Opus", "role": "Content"},
    "worker": {"type": "openclaw", "model": "Haiku 4.5", "role": "Task Execution"},
    "dandan": {"type": "openclaw", "model": "OpenRouter free", "role": "DNU Lead"},
    "gemma": {"type": "openclaw", "model": "Gemma 4 31B", "role": "Bulk Tasks"},
    "mizan": {"type": "openclaw", "model": "Haiku", "role": "Business Agent"},
    "river": {"type": "tmux", "model": "Gemini 3.1 Pro", "role": "Oracle (dormant)"},
    "cyrus": {"type": "remote", "model": "Claude Code", "role": "Mac Frontend"},
    "antigravity": {"type": "remote", "model": "Gemini", "role": "Google IDE"},
}


INTERNAL_STATUS_AGENTS: frozenset[str] = frozenset(
    {
        "sos-mcp-sse",
        "sos-squad",
        "sovereign-loop",
        "calcifer",
        "lifecycle",
        "task-poller",
        "wake-daemon",
    }
)


def text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


async def get_agent_statuses(redis_client: Any) -> list[dict[str, Any]]:
    """Get status of known agents from tmux plus Redis activity."""
    statuses: list[dict[str, Any]] = []
    for name, info in KNOWN_AGENTS.items():
        status = "unknown"

        if info["type"] == "tmux":
            try:
                result = subprocess.run(
                    ["tmux", "has-session", "-t", name],
                    capture_output=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    cap = subprocess.run(
                        ["tmux", "capture-pane", "-t", name, "-p"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    last_lines = " ".join(cap.stdout.strip().split("\n")[-3:]).lower()
                    if any(p in last_lines for p in ["❯", "›", "$ ", "waiting", "you:"]):
                        status = "idle"
                    else:
                        status = "busy"
                else:
                    status = "dead"
            except Exception:
                status = "dead"
        else:
            status = await _redis_agent_status(redis_client, name)

        statuses.append(
            {
                "agent": name,
                "type": info["type"],
                "model": info["model"],
                "role": info["role"],
                "status": status,
            }
        )
    return statuses


async def _redis_agent_status(redis_client: Any, name: str) -> str:
    try:
        stream = f"sos:stream:sos:channel:private:agent:{name}"
        msgs = await redis_client.xrevrange(stream, count=1)
        if msgs:
            last_ts = float(msgs[0][0].split("-")[0]) / 1000
            age_min = (time.time() - last_ts) / 60
            return "active" if age_min < 60 else "idle"
        stream2 = f"sos:stream:global:agent:{name}"
        msgs2 = await redis_client.xrevrange(stream2, count=1)
        if msgs2:
            last_ts = float(msgs2[0][0].split("-")[0]) / 1000
            age_min = (time.time() - last_ts) / 60
            return "active" if age_min < 60 else "idle"
        return "idle"
    except Exception:
        return "unknown"


def get_service_statuses_sync() -> list[dict[str, str]]:
    """Check user-systemd service statuses."""
    services = [
        "sos-mcp-sse",
        "sos-squad",
        "sovereign-loop",
        "calcifer",
        "agent-wake-daemon",
        "bus-bridge",
        "kasra-agent-watchdog",
        "mumcp-agent-watchdog",
    ]
    statuses: list[dict[str, str]] = []
    env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    for service in services:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", f"{service}.service"],
                capture_output=True,
                text=True,
                timeout=3,
                env=env,
            )
            state = result.stdout.strip()
        except Exception:
            state = "unknown"
        statuses.append({"service": service, "status": state})
    return statuses


async def project_agents(redis_client: Any, *, stream_prefix: str) -> set[str]:
    agents: set[str] = set()
    pattern = f"{stream_prefix}:agent:*"
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
        for key in keys:
            agents.add(str(key).split(":")[-1])
        if cursor == 0:
            break
    return agents - set(INTERNAL_STATUS_AGENTS)


def task_counts(
    *,
    squad_service_url: str,
    squad_system_token: str | None,
    requests_get: Callable[..., Any] = requests.get,
) -> dict[str, int]:
    if not squad_system_token:
        return {}
    try:
        response = requests_get(
            f"{squad_service_url}/tasks?limit=500",
            headers={"Authorization": f"Bearer {squad_system_token}"},
            timeout=5,
        )
        if response.ok:
            tasks = response.json()
            return dict(Counter(task.get("status", "?") for task in tasks))
    except Exception:
        return {}
    return {}


def render_status(
    *,
    agent_statuses: list[dict[str, Any]],
    service_statuses: list[dict[str, str]],
    task_count_by_status: dict[str, int],
) -> dict[str, Any]:
    lines = ["# SOS Status\n"]

    lines.append("## Agents")
    for agent in sorted(agent_statuses, key=lambda item: item["status"]):
        icon = {"idle": "🟢", "busy": "🔵", "active": "🟡", "dead": "🔴"}.get(agent["status"], "⚪")
        lines.append(
            f"{icon} **{agent['agent']}** ({agent['model']}) — {agent['role']} [{agent['status']}]"
        )

    lines.append("\n## Services")
    for service in service_statuses:
        icon = "🟢" if service["status"] == "active" else "🔴"
        lines.append(f"{icon} {service['service']}: {service['status']}")

    if task_count_by_status:
        lines.append("\n## Tasks")
        for status, count in sorted(task_count_by_status.items()):
            lines.append(f"- {status}: {count}")

    return text_result("\n".join(lines))


async def handle_status_tool(
    *,
    redis_client: Any,
    is_system: bool,
    project_scope: str | None,
    stream_prefix: str | None,
    squad_service_url: str,
    squad_system_token: str | None,
    get_agents: Callable[[Any], Awaitable[list[dict[str, Any]]]] = get_agent_statuses,
    get_services: Callable[[], list[dict[str, str]]] = get_service_statuses_sync,
    get_task_counts: Callable[..., dict[str, int]] = task_counts,
) -> dict[str, Any]:
    agent_statuses = await get_agents(redis_client)
    service_statuses = await asyncio.get_event_loop().run_in_executor(None, get_services)

    if (not is_system) and project_scope and stream_prefix:
        visible_agents = await project_agents(redis_client, stream_prefix=stream_prefix)
        agent_statuses = [agent for agent in agent_statuses if agent.get("agent") in visible_agents]

    counts = get_task_counts(
        squad_service_url=squad_service_url,
        squad_system_token=squad_system_token,
    )
    return render_status(
        agent_statuses=agent_statuses,
        service_statuses=service_statuses,
        task_count_by_status=counts,
    )
