"""Operator snapshot command for SOS.

The command is intentionally best-effort: a fresh public checkout should be
able to run it even when Redis, Squad auth, or optional services are absent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

HttpGet = Callable[[str, float, Mapping[str, str] | None], tuple[int | None, str]]


@dataclass(frozen=True)
class ServiceProbe:
    name: str
    url: str
    status: str
    detail: str


@dataclass(frozen=True)
class RedisSnapshot:
    status: str
    detail: str
    agents: list[str]
    streams: list[dict[str, Any]]
    failed_wakeups: list[dict[str, Any]]
    recent_gate_events: list[dict[str, Any]]


@dataclass(frozen=True)
class TaskSnapshot:
    status: str
    detail: str
    stuck_tasks: int | None


@dataclass(frozen=True)
class OperatorSnapshot:
    services: list[ServiceProbe]
    redis: RedisSnapshot
    tasks: TaskSnapshot


DEFAULT_SERVICES: tuple[tuple[str, str, str], ...] = (
    ("engine", "SOS_ENGINE_URL", "http://127.0.0.1:6060"),
    ("mcp", "SOS_MCP_HEALTH_URL", "http://127.0.0.1:6070"),
    ("bus-bridge", "SOS_BUS_URL", "http://127.0.0.1:6380"),
    ("registry", "SOS_REGISTRY_URL", "http://127.0.0.1:6067"),
    ("squad", "SOS_SQUAD_URL", "http://127.0.0.1:8060"),
    ("saas", "SOS_SAAS_URL", "http://127.0.0.1:8075"),
    ("docs", "SOS_DOCS_URL", "http://127.0.0.1:8085"),
)


def _redact_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.password:
        return value
    username = parts.username or ""
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{username}:<redacted>@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _default_http_get(
    url: str,
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> tuple[int | None, str]:
    try:
        response = httpx.get(url, headers=dict(headers or {}), timeout=timeout)
    except Exception as exc:
        return None, exc.__class__.__name__
    return response.status_code, response.text[:4000].replace("\n", " ")


def _health_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/health"):
        return trimmed
    return f"{trimmed}/health"


def service_snapshot(
    env: Mapping[str, str] | None = None,
    *,
    http_get: HttpGet = _default_http_get,
    timeout: float = 2.0,
) -> list[ServiceProbe]:
    env = env or os.environ
    probes: list[ServiceProbe] = []
    for name, env_key, default_url in DEFAULT_SERVICES:
        base_url = env.get(env_key, default_url)
        url = _health_url(base_url)
        status_code, detail = http_get(url, timeout, None)
        if status_code == 200:
            status = "ok"
        elif status_code is None:
            status = "unavailable"
        else:
            status = "warn"
        probes.append(ServiceProbe(name=name, url=url, status=status, detail=detail))
    return probes


def redis_snapshot(env: Mapping[str, str] | None = None) -> RedisSnapshot:
    env = env or os.environ
    redis_url = env.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    try:
        import redis

        client = redis.Redis.from_url(redis_url, decode_responses=True)
        client.ping()
    except Exception as exc:
        return RedisSnapshot(
            status="unavailable",
            detail=f"{_redact_url(redis_url)}: {exc.__class__.__name__}",
            agents=[],
            streams=[],
            failed_wakeups=[],
            recent_gate_events=[],
        )

    agents: list[str] = []
    streams: list[dict[str, Any]] = []
    failed_wakeups: list[dict[str, Any]] = []
    recent_gate_events: list[dict[str, Any]] = []

    for key in client.scan_iter(match="sos:registry:*", count=100):
        parts = str(key).split(":")
        if parts:
            agents.append(parts[-1])

    for key in client.scan_iter(match="sos:*", count=200):
        key_s = str(key)
        try:
            key_type = client.type(key_s)
        except Exception:
            continue
        if key_type != "stream":
            continue
        try:
            length = int(client.xlen(key_s))
        except Exception:
            length = 0
        stream_info = {"name": key_s, "length": length}
        streams.append(stream_info)
        lowered = key_s.lower()
        if any(token in lowered for token in ("wake", "failed", "failure", "dlq", "dead")):
            failed_wakeups.append(stream_info)
        if any(token in lowered for token in ("gate", "health", "audit")):
            recent_gate_events.append(stream_info)

    streams.sort(key=lambda item: (-int(item["length"]), str(item["name"])))
    failed_wakeups.sort(key=lambda item: (-int(item["length"]), str(item["name"])))
    recent_gate_events.sort(key=lambda item: (-int(item["length"]), str(item["name"])))
    return RedisSnapshot(
        status="ok",
        detail=f"{_redact_url(redis_url)} connected",
        agents=sorted(set(agents))[:25],
        streams=streams[:10],
        failed_wakeups=failed_wakeups[:10],
        recent_gate_events=recent_gate_events[:10],
    )


def task_snapshot(
    env: Mapping[str, str] | None = None,
    *,
    http_get: HttpGet = _default_http_get,
    timeout: float = 2.0,
) -> TaskSnapshot:
    env = env or os.environ
    token = env.get("SOS_SQUAD_TOKEN") or env.get("SOS_SQUAD_SYSTEM_TOKEN")
    if not token:
        return TaskSnapshot(
            status="unavailable",
            detail="SOS_SQUAD_TOKEN or SOS_SQUAD_SYSTEM_TOKEN not set",
            stuck_tasks=None,
        )
    base_url = env.get("SOS_SQUAD_URL", "http://127.0.0.1:8060").rstrip("/")
    url = f"{base_url}/tasks?status=blocked&limit=25"
    status_code, detail = http_get(url, timeout, {"Authorization": f"Bearer {token}"})
    if status_code != 200:
        return TaskSnapshot(status="warn", detail=f"{status_code}: {detail}", stuck_tasks=None)
    try:
        tasks = json.loads(detail)
    except Exception:
        return TaskSnapshot(
            status="warn",
            detail="blocked-task response was not JSON",
            stuck_tasks=None,
        )
    count = len(tasks) if isinstance(tasks, list) else None
    return TaskSnapshot(status="ok", detail="blocked-task query succeeded", stuck_tasks=count)


def collect_snapshot(
    env: Mapping[str, str] | None = None,
    *,
    http_get: HttpGet = _default_http_get,
) -> OperatorSnapshot:
    env = env or os.environ
    return OperatorSnapshot(
        services=service_snapshot(env, http_get=http_get),
        redis=redis_snapshot(env),
        tasks=task_snapshot(env, http_get=http_get),
    )


def _mark(status: str) -> str:
    return {"ok": "[OK]", "warn": "[!!]", "unavailable": "[--]"}.get(status, "[??]")


def render_text(snapshot: OperatorSnapshot) -> str:
    lines = ["SOS Operator Snapshot", "=" * 40, "", "Services"]
    for probe in snapshot.services:
        lines.append(f"{_mark(probe.status)} {probe.name}: {probe.url}")

    lines.extend(["", "Redis / Bus"])
    lines.append(f"{_mark(snapshot.redis.status)} {snapshot.redis.detail}")
    lines.append(f"agents: {len(snapshot.redis.agents)}")
    if snapshot.redis.agents:
        lines.append("agent_sample: " + ", ".join(snapshot.redis.agents[:10]))
    lines.append("top_streams:")
    for item in snapshot.redis.streams[:5]:
        lines.append(f"  - {item['name']} ({item['length']})")
    if not snapshot.redis.streams:
        lines.append("  - none")

    lines.extend(["", "Wakeups / Gates"])
    lines.append(f"failed_wakeup_streams: {len(snapshot.redis.failed_wakeups)}")
    for item in snapshot.redis.failed_wakeups[:5]:
        lines.append(f"  - {item['name']} ({item['length']})")
    lines.append(f"recent_gate_event_streams: {len(snapshot.redis.recent_gate_events)}")
    for item in snapshot.redis.recent_gate_events[:5]:
        lines.append(f"  - {item['name']} ({item['length']})")

    lines.extend(["", "Tasks"])
    stuck = "unknown" if snapshot.tasks.stuck_tasks is None else str(snapshot.tasks.stuck_tasks)
    lines.append(f"{_mark(snapshot.tasks.status)} stuck_or_blocked_tasks: {stuck}")
    lines.append(f"detail: {snapshot.tasks.detail}")
    return "\n".join(lines)


def render_json(snapshot: OperatorSnapshot) -> str:
    return json.dumps(asdict(snapshot), indent=2, sort_keys=True)


def run_operator_command(args: Any) -> int:
    snapshot = collect_snapshot()
    if getattr(args, "json", False):
        print(render_json(snapshot))
    else:
        print(render_text(snapshot))
    return 0
