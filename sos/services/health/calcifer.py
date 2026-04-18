# Moved from scripts/calcifer.py — heartbeat, health checks, task dispatch
#!/usr/bin/env python3
"""
Calcifer — The Fire That Moves the Castle

Autonomous heartbeat loop for Howl's Moving Castle.
Runs every N minutes as a systemd service.

Responsibilities:
  1. Service health checks (Mirror, Redis, OpenClaw gateway)
  2. Task dispatch — assign unblocked backlog tasks to idle agents
  3. Heartbeat — publish pulse to Redis so agents know the castle breathes
  4. Stale task detection — warn about tasks stuck in_progress too long
  5. Agent wake — ping dormant agents with pending work
  6. Incident alerting — post to Discord and restart critical services

"Without me the castle wouldn't move at all." — Calcifer

Agents supported:
  - kasra   (Claude Code in tmux:kasra)
  - river   (Gemini CLI in tmux:river-dev)
  - athena  (OpenClaw)

Run as:
  systemctl --user start calcifer.service
  python3 calcifer.py --once    # One cycle then exit
  python3 calcifer.py --watch   # Continuous mode
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

try:
    import redis as redis_lib
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [calcifer] %(levelname)s %(message)s",
)
logger = logging.getLogger("calcifer")

# ── Config ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(Path.home() / ".env.secrets")

from sos.kernel.settings import get_settings as _get_settings
_calcifer_settings = _get_settings()
MIRROR_URL = _calcifer_settings.services.mirror
SQUAD_URL = _calcifer_settings.services.squad_url
REDIS_URL = _calcifer_settings.redis.url
REDIS_PASSWORD = _calcifer_settings.redis.password_str
CYCLE_SECONDS = int(os.environ.get("CALCIFER_CYCLE", "600"))  # 10 minutes
STALE_IN_PROGRESS_HOURS = int(os.environ.get("CALCIFER_STALE_IN_PROGRESS_HOURS", "2"))
CLAIMED_STALE_HOURS = int(os.environ.get("CALCIFER_CLAIMED_STALE_HOURS", "1"))
OPENCLAW_UNRESPONSIVE_MINUTES = int(os.environ.get("CALCIFER_OPENCLAW_UNRESPONSIVE_MINUTES", "60"))
DISCORD_ALERT_SCRIPT = str(Path.home() / "scripts" / "discord-reply.sh")
SYSTEMD_RESTART_UNITS = {
    "mirror": "mirror-api.service",
    "squad": "sos-squad.service",
    "openclaw": "openclaw-gateway.service",
}

# ── Self-Healing ───────────────────────────────────────────────────────────
SERVICE_TO_UNIT = {
    "mirror": "mirror",
    "squad": "sos-squad",
    "mcp_sse": "sos-mcp-sse",
    "dashboard": "dashboard",
    "calcifer": "calcifer",  # can't heal itself
    "sentinel": "sentinel",
    "wake_daemon": "agent-wake-daemon",
    "bus_bridge": "bus-bridge",
}

# Health endpoints per service (for recovery verification)
SERVICE_HEALTH_ENDPOINTS: dict[str, str] = {
    "mirror": f"{MIRROR_URL}/health",
    "squad": f"{SQUAD_URL}/health",
}

# Track healing attempts: {service_name: [(timestamp, success), ...]}
_heal_attempts: dict[str, list[tuple[float, bool]]] = defaultdict(list)
_HEAL_MAX_ATTEMPTS_PER_HOUR = 3
_HEAL_COOLDOWN_SECONDS = 10


def _prune_old_attempts(service_name: str) -> None:
    """Remove healing attempts older than 1 hour."""
    cutoff = time.time() - 3600
    _heal_attempts[service_name] = [
        (ts, ok) for ts, ok in _heal_attempts[service_name] if ts > cutoff
    ]


def _emit_health_event(event_type: str, service_name: str) -> None:
    """Fire-and-forget emit to the SOS EventBus (async, best-effort)."""
    try:
        from sos.kernel.events import EventBus, HEALTH_DEGRADED, HEALTH_RECOVERED

        resolved_type = HEALTH_RECOVERED if event_type == "recovered" else HEALTH_DEGRADED
        bus = EventBus()

        async def _emit() -> None:
            try:
                await bus.emit(resolved_type, {"service": service_name}, source="calcifer")
            except Exception:
                pass

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            asyncio.run(_emit())
    except ImportError:
        pass  # events module optional


def _escalate_to_athena(service_name: str) -> None:
    """Send escalation message to Athena via Redis bus."""
    r = get_redis()
    if not r:
        return
    try:
        r.publish("sos:wake:athena", json.dumps({
            "type": "self_heal_failed",
            "source": "calcifer",
            "text": f"Service {service_name} failed to recover after restart. Manual intervention needed.",
            "service": service_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
    except Exception as e:
        logger.warning(f"Escalation to athena failed: {e}")


def _verify_service_health(service_name: str) -> bool:
    """Check if a service is healthy after restart."""
    endpoint = SERVICE_HEALTH_ENDPOINTS.get(service_name)
    if endpoint:
        try:
            resp = requests.get(endpoint, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    # For services without HTTP health endpoints, check systemd status
    unit = SERVICE_TO_UNIT.get(service_name)
    if unit:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", unit],
                capture_output=True, text=True, timeout=5, env=systemd_user_env(),
            )
            return result.stdout.strip() == "active"
        except Exception:
            return False
    return False


def self_heal(service_name: str) -> dict:
    """Attempt to restart a failed service and verify recovery.

    Healing cascade:
    1. Detect failure (health check returns unhealthy)
    2. Attempt restart via systemd
    3. Wait 10 seconds
    4. Verify recovery (health check again)
    5. If recovered: log success, emit health.recovered event
    6. If still down: escalate to Athena via bus, emit health.degraded event

    Returns: {"service": name, "action": "restarted"|"escalated"|"skipped", "success": bool}
    """
    result: dict = {"service": service_name, "action": "skipped", "success": False}

    # Can't restart yourself
    if service_name == "calcifer":
        logger.warning("Cannot self-heal calcifer — skipping")
        return result

    unit = SERVICE_TO_UNIT.get(service_name)
    if not unit:
        # Try SYSTEMD_RESTART_UNITS as fallback
        unit = SYSTEMD_RESTART_UNITS.get(service_name)
    if not unit:
        logger.warning(f"No systemd unit mapped for {service_name} — skipping self-heal")
        return result

    # Rate limit: max 3 attempts per service per hour
    _prune_old_attempts(service_name)
    if len(_heal_attempts[service_name]) >= _HEAL_MAX_ATTEMPTS_PER_HOUR:
        logger.warning(
            f"Self-heal rate limit hit for {service_name} "
            f"({len(_heal_attempts[service_name])} attempts in last hour) — skipping"
        )
        _escalate_to_athena(service_name)
        _emit_health_event("degraded", service_name)
        result["action"] = "escalated"
        return result

    # Attempt restart
    logger.info(f"Self-healing {service_name} (unit: {unit})...")
    restarted = restart_systemd_unit(unit)
    if not restarted:
        logger.error(f"Self-heal restart command failed for {service_name}")
        _heal_attempts[service_name].append((time.time(), False))
        _escalate_to_athena(service_name)
        _emit_health_event("degraded", service_name)
        result["action"] = "escalated"
        return result

    # Wait for service to come up
    time.sleep(_HEAL_COOLDOWN_SECONDS)

    # Verify recovery
    recovered = _verify_service_health(service_name)
    _heal_attempts[service_name].append((time.time(), recovered))

    if recovered:
        logger.info(f"Self-healed {service_name} successfully")
        _emit_health_event("recovered", service_name)
        result["action"] = "restarted"
        result["success"] = True
    else:
        logger.error(f"Self-heal failed for {service_name} — still unhealthy after restart")
        _escalate_to_athena(service_name)
        _emit_health_event("degraded", service_name)
        result["action"] = "escalated"

    return result


# ── Agent Registry ──────────────────────────────────────────────────────────
# Fallback agent registry — used when Squad Service is unavailable.
# Prefer dynamic registry: GET /agents on Squad Service (:8060)
FALLBACK_AGENTS = {
    "kasra": {
        "type": "tmux",
        "tmux_session": "kasra",
        "idle_patterns": ["❯", "$ "],
        "busy_patterns": ["Transmuting", "Churning", "Baking", "Warping", "Thinking"],
        "skills": ["backend", "frontend", "infrastructure", "nginx", "api", "database", "typescript", "python"],
        "max_concurrent": 1,
    },
    "river": {
        "type": "tmux",
        "tmux_session": "river",
        "idle_patterns": ["◆", "> ", "❯"],
        "busy_patterns": ["Thinking", "Writing", "Generating", "◒"],
        "skills": ["strategy", "frc", "content", "oracle", "memory", "distillation", "creative"],
        "max_concurrent": 1,
    },
    "athena": {
        "type": "openclaw",
        "skills": ["architecture", "design", "planning", "coordination", "review"],
        "max_concurrent": 2,
    },
    "worker": {
        "type": "openclaw",
        "skills": ["seo", "content", "audit", "analysis", "reporting", "squad_tasks"],
        "max_concurrent": 3,
    },
    "dandan": {
        "type": "openclaw",
        "skills": ["dental", "outreach", "leads", "google_maps"],
        "max_concurrent": 2,
    },
}

# Cached dynamic registry (refreshed each cycle)
_cached_agents: dict | None = None
_cached_agents_time: float = 0.0
_AGENT_CACHE_TTL = 60  # seconds


def get_registered_agents(squad_url: str = SQUAD_URL) -> dict:
    """Fetch agents from Squad Service dynamic registry.

    Falls back to FALLBACK_AGENTS if Squad Service is down.
    Caches result for 60s to avoid hammering the service within a cycle.
    """
    global _cached_agents, _cached_agents_time

    now = time.time()
    if _cached_agents is not None and (now - _cached_agents_time) < _AGENT_CACHE_TTL:
        return _cached_agents

    try:
        resp = requests.get(f"{squad_url}/agents", timeout=5)
        if resp.status_code == 200:
            agents_list = resp.json()
            if isinstance(agents_list, dict):
                agents_list = agents_list.get("agents", [])
            result: dict = {}
            for agent in agents_list:
                name = agent.get("name")
                if not name:
                    continue
                result[name] = {
                    "type": agent.get("framework", "custom"),
                    "skills": agent.get("skills", []),
                    "max_concurrent": agent.get("max_concurrent", 1),
                }
                # Preserve tmux metadata from fallback if agent type is tmux
                if name in FALLBACK_AGENTS and FALLBACK_AGENTS[name].get("type") == "tmux":
                    fb = FALLBACK_AGENTS[name]
                    result[name].setdefault("tmux_session", fb.get("tmux_session", name))
                    result[name].setdefault("idle_patterns", fb.get("idle_patterns", []))
                    result[name].setdefault("busy_patterns", fb.get("busy_patterns", []))
            if result:
                _cached_agents = result
                _cached_agents_time = now
                return result
    except Exception as e:
        logger.warning(f"Squad Service unavailable, using fallback agents: {e}")

    _cached_agents = FALLBACK_AGENTS
    _cached_agents_time = now
    return FALLBACK_AGENTS


# ── Redis ───────────────────────────────────────────────────────────────────
_redis = None


def get_redis_url() -> str:
    """Prefer an authenticated Redis URL when only a local URL is configured."""
    if REDIS_PASSWORD and REDIS_URL == "redis://localhost:6379":
        return f"redis://:{REDIS_PASSWORD}@localhost:6379/0"
    return REDIS_URL

def get_redis():
    global _redis
    if not REDIS_AVAILABLE:
        return None
    if _redis is None:
        try:
            _redis = redis_lib.from_url(get_redis_url(), decode_responses=True, socket_timeout=3)
            _redis.ping()
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}")
            _redis = None
    return _redis


def systemd_user_env() -> dict:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    return env


def parse_iso_datetime(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def fetch_tasks(base_url: str, params: Optional[dict] = None) -> list[dict]:
    try:
        resp = requests.get(f"{base_url}/tasks", params=params or {}, timeout=5)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        if isinstance(payload, dict):
            return payload.get("tasks", []) or []
        if isinstance(payload, list):
            return payload
    except Exception as e:
        logger.warning(f"Task fetch failed from {base_url}: {e}")
    return []


def alert_discord(message: str) -> bool:
    try:
        subprocess.run(
            ["bash", DISCORD_ALERT_SCRIPT, "system", "alerts", f"ALERT: {message}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return True
    except Exception as e:
        logger.warning(f"Discord alert failed: {e}")
        return False


def restart_systemd_unit(unit: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", unit],
            capture_output=True,
            text=True,
            timeout=30,
            env=systemd_user_env(),
        )
        if result.returncode == 0:
            logger.warning(f"Auto-restarted {unit}")
            return True
        logger.warning(f"Auto-restart failed for {unit}: {result.stderr.strip() or result.stdout.strip()}")
    except Exception as e:
        logger.warning(f"Auto-restart exception for {unit}: {e}")
    return False


def publish_heartbeat(cycle_num: int, health: dict):
    """Publish Calcifer's heartbeat to Redis."""
    r = get_redis()
    if not r:
        return
    pulse = {
        "type": "calcifer_pulse",
        "source": "calcifer",
        "cycle": cycle_num,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health": health,
    }
    try:
        r.publish("calcifer:pulse", json.dumps(pulse))
        r.xadd("sos:stream:calcifer", {"data": json.dumps(pulse)}, maxlen=100)
        logger.debug(f"Heartbeat published (cycle {cycle_num})")
    except Exception as e:
        logger.warning(f"Heartbeat publish failed: {e}")


def wake_agent(agent_id: str, message: str):
    """Send wake signal to an agent via Redis."""
    r = get_redis()
    if not r:
        return
    try:
        r.publish(f"sos:wake:{agent_id}", json.dumps({
            "type": "calcifer_wake",
            "source": "calcifer",
            "text": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
    except Exception as e:
        logger.warning(f"Wake signal failed for {agent_id}: {e}")


# ── Health Checks ───────────────────────────────────────────────────────────
def check_mirror() -> dict:
    try:
        resp = requests.get(f"{MIRROR_URL}/health", timeout=5)
        return {"status": "ok", "code": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def check_squad_service() -> dict:
    try:
        resp = requests.get(f"{SQUAD_URL}/health", timeout=5)
        return {"status": "ok" if resp.status_code == 200 else "down", "code": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def check_redis() -> dict:
    r = get_redis()
    if r:
        return {"status": "ok"}
    return {"status": "down"}


def check_openclaw() -> dict:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "openclaw-gateway.service"],
            capture_output=True, text=True, timeout=5, env=systemd_user_env()
        )
        status = result.stdout.strip()
        return {"status": "ok" if status == "active" else "down", "systemd": status}
    except Exception as e:
        return {"status": "unknown", "error": str(e)}


def check_tmux_session(session_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def run_health_checks() -> dict:
    health = {
        "mirror": check_mirror(),
        "redis": check_redis(),
        "squad": check_squad_service(),
        "openclaw": check_openclaw(),
        "agents": {},
    }
    agents = get_registered_agents()
    for agent_id, config in agents.items():
        if config.get("type") == "tmux":
            session = config.get("tmux_session", agent_id)
            alive = check_tmux_session(session)
            health["agents"][agent_id] = {"tmux": session, "alive": alive}
        else:
            health["agents"][agent_id] = {"type": config.get("type", "openclaw")}
    return health


# ── Task Dispatch ──────────────────────────────────────────────────────────
def get_unblocked_tasks() -> list[dict]:
    try:
        resp = requests.get(f"{MIRROR_URL}/tasks", timeout=5)
        if resp.status_code != 200:
            return []
        raw = resp.json()
        all_tasks = raw.get("tasks", []) if isinstance(raw, dict) else raw
        # STRICT: only backlog status — ignore done, blocked, in_progress, canceled
        tasks = [t for t in all_tasks if t.get("status") == "backlog"]

        unblocked = []
        for t in tasks:
            blocked_by = t.get("blocked_by") or []
            if not blocked_by:
                unblocked.append(t)
                continue
            all_done = True
            for bid in blocked_by:
                try:
                    bresp = requests.get(f"{MIRROR_URL}/tasks/{bid}", timeout=3)
                    if bresp.status_code == 200:
                        if bresp.json().get("status") not in ("done", "canceled"):
                            all_done = False
                            break
                except Exception:
                    all_done = False
                    break
            if all_done:
                unblocked.append(t)

        prio = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
        unblocked.sort(key=lambda t: prio.get(t.get("priority", "low"), 4))
        return unblocked
    except Exception as e:
        logger.error(f"Task fetch failed: {e}")
        return []


def get_in_progress_count(agent_id: str) -> int:
    try:
        resp = requests.get(
            f"{MIRROR_URL}/tasks",
            params={"agent": agent_id, "status": "in_progress"},
            timeout=5
        )
        if resp.status_code == 200:
            return resp.json().get("count", 0)
    except Exception:
        pass
    return 0


def get_tasks_for_stale_detection() -> list[dict]:
    merged: dict[str, dict] = {}
    for source, base_url in (("mirror", MIRROR_URL), ("squad", SQUAD_URL)):
        for task in fetch_tasks(base_url):
            task_id = task.get("id")
            if not task_id:
                continue
            merged.setdefault(task_id, {**task, "_source": source})
    return list(merged.values())


def get_pending_tasks_for_agent(agent_id: str) -> list[dict]:
    pending: list[dict] = []
    seen: set[str] = set()
    for base_url in (MIRROR_URL, SQUAD_URL):
        for task in fetch_tasks(base_url):
            task_id = task.get("id")
            if not task_id or task_id in seen:
                continue
            if task.get("agent") != agent_id:
                continue
            if task.get("status") not in {"backlog", "claimed", "in_progress"}:
                continue
            seen.add(task_id)
            pending.append(task)
    return pending


def is_agent_idle(agent_id: str) -> bool:
    config = get_registered_agents().get(agent_id, {})
    if config.get("type") == "openclaw":
        return True  # OpenClaw handles its own queue

    session = config.get("tmux_session", agent_id)
    if not check_tmux_session(session):
        return False

    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True, text=True, timeout=5
        )
        tail = "\n".join(result.stdout.strip().split("\n")[-5:])
        idle_hit = any(p in tail for p in config.get("idle_patterns", ["❯"]))
        busy_hit = any(p in tail for p in config.get("busy_patterns", []))
        return idle_hit and not busy_hit
    except Exception:
        return False


def send_task_to_agent(agent_id: str, task: dict) -> bool:
    config = get_registered_agents().get(agent_id, {})
    title = task["title"]
    desc = task.get("description", "")
    task_id = task["id"]
    priority = task.get("priority", "medium")

    prompt = (
        f"TASK [{priority.upper()}] {task_id}:\n{title}\n\n{desc}\n\n"
        f"When done, report what you did. Do not ask for clarification — execute."
    )

    if config.get("type") == "openclaw":
        # Send via Redis to OpenClaw agent
        r = get_redis()
        if r:
            r.xadd(
                f"sos:stream:sos:channel:private:agent:{agent_id}",
                {"type": "task_dispatch", "source": "calcifer", "payload": json.dumps({
                    "task_id": task_id, "title": title, "priority": priority, "description": desc
                })}
            )
            r.publish(f"sos:wake:{agent_id}", json.dumps({"text": f"New task: {title}"}))
    else:
        # Send via tmux
        session = config.get("tmux_session", agent_id)
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "-l", "--", prompt],
                timeout=5
            )
            time.sleep(0.2)
            subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], timeout=5)
        except Exception as e:
            logger.error(f"tmux send failed for {agent_id}: {e}")
            return False

    # Mark as in_progress
    try:
        requests.put(f"{MIRROR_URL}/tasks/{task_id}", json={"status": "in_progress"}, timeout=5)
    except Exception:
        pass

    # Notify via Redis
    r = get_redis()
    if r:
        try:
            r.xadd(
                f"sos:stream:sos:channel:private:agent:{agent_id}", "*",
                {"type": "task_dispatched", "source": "calcifer",
                 "payload": json.dumps({"task_id": task_id, "title": title})}
            )
        except Exception:
            pass

    logger.info(f"Dispatched [{priority}] '{title[:50]}' → {agent_id}")
    return True


def check_skill_match(agent_id: str, task: dict) -> bool:
    """Does this agent have skills relevant to this task?"""
    agent_skills = get_registered_agents().get(agent_id, {}).get("skills", [])
    if not agent_skills:
        return True  # No skill filter = accepts all

    task_tags = task.get("tags") or []
    task_agent = task.get("agent", "")

    # Explicit assignment
    if task_agent and task_agent != "athena":
        return task_agent == agent_id

    # Unassigned — check skill overlap
    text = (task.get("title", "") + " " + task.get("description", "")).lower()
    return any(skill in text for skill in agent_skills) or not task_agent


def check_openclaw_agent_responsiveness() -> dict:
    agents = get_registered_agents()
    openclaw_agents = [agent_id for agent_id, config in agents.items() if config.get("type") == "openclaw"]
    threshold_ms = OPENCLAW_UNRESPONSIVE_MINUTES * 60 * 1000
    details: dict[str, dict] = {}
    unresponsive: list[dict] = []

    for agent_id in openclaw_agents:
        pending_tasks = get_pending_tasks_for_agent(agent_id)
        try:
            result = subprocess.run(
                ["openclaw", "sessions", "--agent", agent_id, "--json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "openclaw sessions failed")
            payload = json.loads(result.stdout or "{}")
            sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
            latest = min(
                sessions,
                key=lambda s: int(s.get("ageMs") or 10**18),
            ) if sessions else None
            if not latest:
                details[agent_id] = {"status": "idle" if not pending_tasks else "down", "reason": "no openclaw sessions"}
                if pending_tasks:
                    unresponsive.append({
                        "agent": agent_id,
                        "reason": "no openclaw sessions",
                        "pending_tasks": [task.get("id") for task in pending_tasks[:5]],
                    })
                continue
            age_ms = int(latest.get("ageMs") or 0)
            details[agent_id] = {
                "status": "ok" if age_ms <= threshold_ms or not pending_tasks else "stale",
                "age_ms": age_ms,
                "session_id": latest.get("sessionId"),
                "pending_tasks": [task.get("id") for task in pending_tasks[:5]],
            }
            if pending_tasks and age_ms > threshold_ms:
                unresponsive.append({
                    "agent": agent_id,
                    "reason": f"no heartbeat for {age_ms // 60000}m",
                    "session_id": latest.get("sessionId"),
                    "pending_tasks": [task.get("id") for task in pending_tasks[:5]],
                })
        except Exception as e:
            details[agent_id] = {"status": "unknown", "error": str(e), "pending_tasks": [task.get("id") for task in pending_tasks[:5]]}
            if pending_tasks:
                unresponsive.append({"agent": agent_id, "reason": str(e), "pending_tasks": [task.get("id") for task in pending_tasks[:5]]})

    return {
        "status": "ok" if not unresponsive else "degraded",
        "agents": details,
        "unresponsive": unresponsive,
    }


# ── Wire 6: Conductance Network (FRC 531) ─────────────────────────────────────
# Moved to sos.kernel.conductance in v0.4.5 Wave 8 — three services
# (health/calcifer, feedback/loop, journeys/tracker) all read the same G
# matrix, so it belongs in the kernel alongside bus/auth/health primitives.
from sos.kernel.conductance import (
    CONDUCTANCE_ALPHA,
    CONDUCTANCE_FILE,
    CONDUCTANCE_GAMMA,
    _load_conductance,
    _save_conductance,
    conductance_decay,
    conductance_update,
)


def _get_agent_task_score(agent_id: str, task: dict) -> float:
    """Wire 6: Score an agent-task pair using conductance + coherence.

    Score = G[agent][task_skill] + C * bounty_value
    High conductance (proven flow) + high coherence = best match.
    """
    G = _load_conductance()
    agent_G = G.get(agent_id, {})

    # Extract task skills from labels and type
    task_skills = set(task.get("labels") or [])
    task_text = (task.get("title", "") + " " + task.get("description", "")).lower()
    for skill in ["seo", "content", "ux", "web", "outreach", "dental", "blog"]:
        if skill in task_text:
            task_skills.add(skill)

    # Sum conductance for matching skills
    g_score = sum(agent_G.get(s, 0.0) for s in task_skills)

    # Add bounty value weighted by basic coherence
    bounty_val = float((task.get("bounty") or {}).get("reward", 0))

    return g_score + bounty_val * 0.01  # G dominates, bounty is tiebreaker


# ── Delivered Task Tracking (merged from task-poller) ──────────────────────────
DELIVERED_TASKS_FILE = Path.home() / ".sos" / "state" / "delivered_tasks.json"

_delivered_tasks: set[str] | None = None


def _load_delivered_tasks() -> set[str]:
    global _delivered_tasks
    if _delivered_tasks is not None:
        return _delivered_tasks
    if DELIVERED_TASKS_FILE.exists():
        try:
            data = json.loads(DELIVERED_TASKS_FILE.read_text())
            _delivered_tasks = set(data.get("task_ids", []))
            return _delivered_tasks
        except (json.JSONDecodeError, OSError):
            pass
    _delivered_tasks = set()
    return _delivered_tasks


def _mark_delivered(task_id: str) -> None:
    tasks = _load_delivered_tasks()
    tasks.add(task_id)
    # Keep last 1000
    ids = list(tasks)[-1000:]
    DELIVERED_TASKS_FILE.write_text(json.dumps({
        "task_ids": ids,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }))


def _fetch_assigned_tasks() -> list[dict]:
    """Fetch queued tasks assigned to specific agents (from task-poller logic)."""
    from sos.kernel.agent_registry import get_executor_agents, is_coordinator
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    delivered = _load_delivered_tasks()
    assigned: list[dict] = []

    for agent_id in get_executor_agents():
        try:
            resp = requests.get(
                f"{SQUAD_URL}/tasks",
                params={"assignee": agent_id, "status": "queued"},
                headers={"Authorization": f"Bearer {os.environ.get('SOS_SYSTEM_TOKEN', '')}"},
                timeout=5,
            )
            if resp.status_code != 200:
                continue
            payload = resp.json()
            tasks = payload.get("tasks", payload) if isinstance(payload, dict) else payload
            for t in tasks:
                task_id = t.get("id", "")
                if task_id in delivered:
                    continue
                created = parse_iso_datetime(t.get("created_at") or t.get("updated_at"))
                if created and created < cutoff:
                    continue
                assigned.append(t)
        except Exception:
            pass

    return assigned


def run_dispatch_cycle():
    """Assign tasks to idle agents — unified dispatch.

    Combines backlog dispatch + assigned task delivery (merged from task-poller).
    Wire 6 (FRC 531): Conductance-based matching.
    """
    # Merge both task sources: unblocked backlog + specifically assigned
    tasks = get_unblocked_tasks()
    assigned = _fetch_assigned_tasks()
    # Assigned tasks get priority (they were explicitly queued for an agent)
    all_tasks = assigned + [t for t in tasks if t.get("id") not in {a.get("id") for a in assigned}]

    if not all_tasks:
        logger.info("No dispatchable tasks.")
        return 0

    agents = get_registered_agents()

    # Build list of idle, available agents
    available: list[str] = []
    for agent_id in agents:
        if get_in_progress_count(agent_id) >= agents[agent_id].get("max_concurrent", 1):
            continue
        if not is_agent_idle(agent_id):
            continue
        available.append(agent_id)

    if not available:
        logger.info("No idle agents available.")
        return 0

    dispatched = 0
    delivered = _load_delivered_tasks()

    for task in all_tasks[:]:
        if not available:
            break

        task_id = task.get("id", "")
        if task_id in delivered:
            continue

        # If task has explicit assignee in available list, dispatch directly
        assignee = task.get("assignee")
        if assignee and assignee in available:
            if send_task_to_agent(assignee, task):
                _mark_delivered(task_id)
                available.remove(assignee)
                dispatched += 1
            continue

        # Otherwise: conductance-based matching
        scored: list[tuple[str, float]] = []
        for agent_id in available:
            if check_skill_match(agent_id, task):
                score = _get_agent_task_score(agent_id, task)
                scored.append((agent_id, score))

        if not scored:
            continue

        scored.sort(key=lambda x: x[1], reverse=True)
        best_agent, best_score = scored[0]

        bounty_val = float((task.get("bounty") or {}).get("reward", 0))
        if bounty_val > 0:
            logger.info(
                f"Wire 6: {best_agent} (G={best_score:.1f}) → "
                f"{task.get('title', '')[:40]} ({bounty_val:.0f} MIND)"
            )

        if send_task_to_agent(best_agent, task):
            _mark_delivered(task_id)
            available.remove(best_agent)
            dispatched += 1

    return dispatched


# ── Stale Task Detection ────────────────────────────────────────────────────
def check_stale_tasks() -> list[dict]:
    """Return tasks stuck in_progress or claimed too long."""
    stale: list[dict] = []
    seen: set[str] = set()
    in_progress_deadline = datetime.now(timezone.utc) - timedelta(hours=STALE_IN_PROGRESS_HOURS)
    claimed_deadline = datetime.now(timezone.utc) - timedelta(hours=CLAIMED_STALE_HOURS)

    for task in get_tasks_for_stale_detection():
        task_id = task.get("id")
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)

        status = task.get("status")
        if status not in {"in_progress", "claimed"}:
            continue

        timestamp_fields = [
            task.get("started_at"),
            task.get("startedAt"),
            task.get("claimed_at"),
            task.get("claimedAt"),
            task.get("updated_at"),
            task.get("updatedAt"),
        ]
        age_dt = next((parse_iso_datetime(value) for value in timestamp_fields if parse_iso_datetime(value)), None)
        if not age_dt:
            continue

        if status == "in_progress" and age_dt < in_progress_deadline:
            stale.append({
                "id": task_id,
                "title": task.get("title", ""),
                "status": status,
                "age_hours": round((datetime.now(timezone.utc) - age_dt).total_seconds() / 3600, 2),
                "reason": f"in_progress > {STALE_IN_PROGRESS_HOURS}h",
            })
        elif status == "claimed" and not task.get("result") and age_dt < claimed_deadline:
            stale.append({
                "id": task_id,
                "title": task.get("title", ""),
                "status": status,
                "age_hours": round((datetime.now(timezone.utc) - age_dt).total_seconds() / 3600, 2),
                "reason": f"claimed > {CLAIMED_STALE_HOURS}h with no result",
            })

    if stale:
        logger.warning(
            "Stale tasks detected: " + ", ".join(
                f"{item['id'][:8]}({item['reason']})" for item in stale[:5]
            )
        )
        r = get_redis()
        if r:
            r.publish("sos:wake:athena", json.dumps({
                "type": "stale_tasks",
                "source": "calcifer",
                "text": f"{len(stale)} stale tasks detected",
                "stale": stale,
            }))
    return stale


# ── Main Loop ───────────────────────────────────────────────────────────────
def run_cycle(cycle_num: int):
    logger.info(f"=== Calcifer cycle {cycle_num} ===")

    # 1. Health
    health = run_health_checks()
    mirror_ok = health["mirror"]["status"] == "ok"
    redis_ok = health["redis"]["status"] == "ok"
    squad_ok = health["squad"]["status"] == "ok"
    logger.info(
        f"Health — mirror:{health['mirror']['status']} redis:{health['redis']['status']} "
        f"squad:{health['squad']['status']} openclaw:{health['openclaw']['status']}"
    )

    # 2. Agent presence
    for agent_id, agent_health in health["agents"].items():
        if agent_health.get("type") != "openclaw":
            alive = agent_health.get("alive", False)
            if not alive:
                logger.info(f"Agent {agent_id}: tmux session not running")

    # 3. Heartbeat
    publish_heartbeat(cycle_num, health)

    issues: list[str] = []
    healed_services: set[str] = set()

    for service_name in ("mirror", "redis", "squad", "openclaw"):
        service_health = health.get(service_name, {})
        if service_health.get("status") != "ok":
            detail = service_health.get("error") or service_health.get("systemd") or service_health.get("code") or "unhealthy"
            heal_result = self_heal(service_name)
            if heal_result["success"]:
                logger.info(f"Self-healed: {service_name}")
                issues.append(f"{service_name} was down ({detail}) — self-healed")
                healed_services.add(service_name)
            else:
                logger.error(f"Self-heal failed: {service_name}, escalated to athena")
                issues.append(f"{service_name} down ({detail}) — escalated")

    # Update health flags if services were healed
    if "mirror" in healed_services:
        mirror_ok = True

    # 4. Stale tasks
    stale_tasks = check_stale_tasks()
    if stale_tasks:
        issues.append(
            f"{len(stale_tasks)} stale tasks: "
            + ", ".join(f"{task['id'][:8]} {task['reason']}" for task in stale_tasks[:3])
        )

    # 5. OpenClaw agent responsiveness
    openclaw_agents = check_openclaw_agent_responsiveness()
    if openclaw_agents.get("unresponsive"):
        issues.append(
            f"{len(openclaw_agents['unresponsive'])} unresponsive OpenClaw agents: "
            + ", ".join(item["agent"] for item in openclaw_agents["unresponsive"][:5])
        )

    # 6. Alert once if anything looks wrong
    if issues:
        alert_discord("; ".join(issues))

    if not mirror_ok:
        logger.warning("Mirror API down — skipping task dispatch")
        return

    # 7. Task dispatch
    dispatched = run_dispatch_cycle()
    if dispatched:
        logger.info(f"Dispatched {dispatched} tasks this cycle")

    logger.info(f"Cycle {cycle_num} complete.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Calcifer — Castle Heartbeat")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--watch", action="store_true", help="Run continuously")
    args = parser.parse_args()

    logger.info("Calcifer awakening...")
    logger.info(f"Mirror: {MIRROR_URL} | Cycle: {CYCLE_SECONDS}s")

    if args.once:
        run_cycle(1)
        return

    # Default: continuous loop (for systemd service)
    cycle_num = 0
    while True:
        cycle_num += 1
        try:
            run_cycle(cycle_num)
        except KeyboardInterrupt:
            logger.info("Calcifer extinguished.")
            break
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
