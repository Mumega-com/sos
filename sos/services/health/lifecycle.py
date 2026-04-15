#!/usr/bin/env python3
"""
Agent Lifecycle Manager — Gap #1 from HARNESS-GAPS.md

Detects dead, stuck, or compacted agents and takes corrective action.
Runs as a systemd service alongside Calcifer, polling every 60 seconds.

States detected:
  - dead: warm agent vanished or active cold worker died unexpectedly
  - parked: cold agent intentionally offline with no active work
  - stuck: no output change for 120 minutes with in_progress tasks
  - compacted: Claude Code context compaction detected
  - idle: agent at prompt with no tasks (healthy, no action needed)

Actions:
  - dead → restart agent session, inject context from Mirror + Squad
  - stuck → send interrupt, escalate if still stuck
  - compacted → send /compact or re-inject current task
  - any event → alert via bus + optional Discord

Run as:
  systemctl --user start agent-lifecycle.service
  python3 -m sos.services.health.lifecycle --once
  python3 -m sos.services.health.lifecycle --watch
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

try:
    import redis as redis_lib
except ImportError:
    print("pip install redis")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".env.secrets")
except ImportError:
    pass

from sos.kernel.agent_registry import get_all_agents, AgentType
from sos.services.health.worker_teardown import prune_stale_workers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [lifecycle] %(levelname)s %(message)s",
)
logger = logging.getLogger("lifecycle")

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.environ.get("LIFECYCLE_POLL_INTERVAL", "60"))
STUCK_THRESHOLD_MINUTES = int(os.environ.get("LIFECYCLE_STUCK_MINUTES", "120"))
SQUAD_URL = os.environ.get("SQUAD_URL", "http://127.0.0.1:8060")
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_URL = os.environ.get("REDIS_URL", f"redis://:{REDIS_PASSWORD}@localhost:6379/0" if REDIS_PASSWORD else "redis://localhost:6379/0")
SQUAD_TOKEN = os.environ.get("SOS_SYSTEM_TOKEN", "")
STATE_DIR = Path.home() / ".sos" / "state"
LOG_DIR = Path.home() / ".sos" / "logs" / "lifecycle"
DISCORD_ALERT_SCRIPT = str(Path.home() / "scripts" / "discord-reply.sh")

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _squad_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SQUAD_TOKEN}"} if SQUAD_TOKEN else {}


# Agent definitions: sourced from the unified agent registry
def _build_agent_defs() -> dict[str, dict]:
    """Convert AgentDef objects from the registry to the dict format lifecycle expects."""
    result = {}
    for name, agent in get_all_agents().items():
        result[name] = {
            "type": agent.type.value,
            "session": agent.session,
            "restart_cmd": agent.restart_cmd,
            "idle_patterns": list(agent.idle_patterns),
            "busy_patterns": list(agent.busy_patterns),
            "compaction_patterns": list(agent.compaction_patterns),
            "warm_policy": agent.warm_policy.value,
        }
    return result


AGENT_DEFS = _build_agent_defs()


# ── Redis ─────────────────────────────────────────────────────────────────────
_redis: Optional[redis_lib.Redis] = None


def get_redis() -> Optional[redis_lib.Redis]:
    global _redis
    if _redis is None:
        try:
            _redis = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
            _redis.ping()
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}")
            _redis = None
    return _redis


# ── State tracking ────────────────────────────────────────────────────────────
def load_agent_state(agent_id: str) -> dict:
    state_file = STATE_DIR / f"{agent_id}.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_agent_state(agent_id: str, state: dict) -> None:
    state_file = STATE_DIR / f"{agent_id}.json"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_file.write_text(json.dumps(state, indent=2))


def _snapshot_agent_context(agent_id: str, state: dict, agent_def: dict) -> None:
    """Save rich working context snapshot for restart resilience (Gap 4).

    Called every cycle when agent is busy. Captures:
    - Current in_progress tasks from Squad
    - Working directory / git branch
    - Last few lines of output (what agent is doing)
    """
    # Current tasks
    try:
        resp = requests.get(
            f"{SQUAD_URL}/tasks",
            params={"agent": agent_id, "status": "in_progress"},
            headers=_squad_headers(),
            timeout=5,
        )
        if resp.status_code == 200:
            tasks = resp.json()
            if isinstance(tasks, dict):
                tasks = tasks.get("tasks", [])
            state["current_tasks"] = [
                {"id": t.get("id", ""), "title": t.get("title", "")}
                for t in tasks[:5]
            ]
    except Exception:
        pass

    # Last output snippet (what agent is actively doing)
    if agent_def.get("type") == "tmux":
        session = agent_def.get("session", agent_id)
        pane = capture_tmux_pane(session, lines=10)
        if pane.strip():
            # Store last meaningful lines (skip blank lines)
            lines = [l for l in pane.strip().split("\n") if l.strip()][-5:]
            state["last_output_snippet"] = "\n".join(lines)

    # Recent bus messages (inbox peek)
    r = get_redis()
    if r:
        try:
            stream_key = f"sos:stream:global:agent:{agent_id}"
            messages = r.xrevrange(stream_key, count=3)
            if messages:
                texts = []
                for m in messages:
                    fields = m[1]
                    # Redis may return bytes or str depending on decode_responses
                    raw = fields.get(b"payload") or fields.get("payload", "")
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    try:
                        payload = json.loads(raw) if raw else {}
                        text = payload.get("text", "")
                    except (json.JSONDecodeError, AttributeError):
                        text = raw
                    if text:
                        texts.append(text[:200])
                if texts:
                    state["recent_bus_messages"] = texts
        except Exception:
            pass

    # Gap 8: Session persistence — git status + working directory
    if agent_def.get("type") == "tmux":
        session = agent_def.get("session", agent_id)
        try:
            # Get working directory from tmux
            proc = subprocess.run(
                ["tmux", "display-message", "-t", session, "-p", "#{pane_current_path}"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                cwd = proc.stdout.strip()
                state["working_directory"] = cwd

                # Get git branch if in a git repo
                git_proc = subprocess.run(
                    ["git", "-C", cwd, "branch", "--show-current"],
                    capture_output=True, text=True, timeout=5,
                )
                if git_proc.returncode == 0:
                    state["git_branch"] = git_proc.stdout.strip()

                # Get brief git status
                git_status = subprocess.run(
                    ["git", "-C", cwd, "status", "--porcelain", "--short"],
                    capture_output=True, text=True, timeout=5,
                )
                if git_status.returncode == 0:
                    status_lines = git_status.stdout.strip().split("\n")[:10]
                    state["git_status"] = status_lines
        except Exception:
            pass


def log_event(agent_id: str, event_type: str, details: str) -> None:
    """Log lifecycle event to file and Redis stream."""
    entry = {
        "agent": agent_id,
        "event": event_type,
        "details": details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # File log
    log_file = LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Redis stream
    r = get_redis()
    if r:
        try:
            r.xadd("sos:stream:lifecycle", {"payload": json.dumps(entry)}, maxlen=1000)
        except Exception:
            pass

    logger.info(f"[{agent_id}] {event_type}: {details}")


# ── Detection ─────────────────────────────────────────────────────────────────
def check_tmux_alive(session: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def capture_tmux_pane(session: str, lines: int = 50) -> str:
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p", "-S", f"-{lines}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _agent_has_active_tasks(agent_id: str) -> bool:
    try:
        resp = requests.get(
            f"{SQUAD_URL}/tasks",
            params={"agent": agent_id, "status": "in_progress"},
            headers=_squad_headers(),
            timeout=5,
        )
        if resp.status_code != 200:
            return False
        tasks = resp.json()
        if isinstance(tasks, dict):
            tasks = tasks.get("tasks", [])
        return bool(tasks)
    except Exception:
        return False


def _parked_override(agent_id: str) -> str | None:
    state = load_agent_state(agent_id)
    parked = state.get("parked")
    parked_reason = state.get("parked_reason") or "agent intentionally parked"
    parked_until = _parse_iso_timestamp(state.get("parked_until"))

    if parked_until and parked_until <= datetime.now(timezone.utc):
        state.pop("parked", None)
        state.pop("parked_reason", None)
        state.pop("parked_until", None)
        save_agent_state(agent_id, state)
        return None

    if parked:
        return str(parked_reason)
    if parked_until and parked_until > datetime.now(timezone.utc):
        return str(parked_reason)
    return None


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def detect_agent_state(agent_id: str, agent_def: dict) -> dict:
    """Detect the current state of an agent.

    Returns: {"state": "dead"|"parked"|"stuck"|"compacted"|"busy"|"idle", "details": str}
    """
    parked_reason = _parked_override(agent_id)
    if parked_reason:
        return {"state": "parked", "details": parked_reason}

    if agent_def.get("type") == "openclaw":
        return _detect_openclaw_state(agent_id, agent_def)

    session = agent_def.get("session", agent_id)
    warm_policy = agent_def.get("warm_policy", "cold")

    # Check if tmux session exists
    if not check_tmux_alive(session):
        if warm_policy == "cold" and not _agent_has_active_tasks(agent_id):
            return {"state": "parked", "details": f"cold agent '{agent_id}' intentionally offline"}
        return {"state": "dead", "details": f"tmux session '{session}' not found"}

    # Capture pane content
    pane = capture_tmux_pane(session)
    if not pane.strip():
        if warm_policy == "cold" and not _agent_has_active_tasks(agent_id):
            return {"state": "parked", "details": f"cold agent '{agent_id}' has no active pane and no active tasks"}
        return {"state": "dead", "details": f"tmux session '{session}' empty pane"}

    tail = "\n".join(pane.strip().split("\n")[-10:])

    # Check for compaction
    compaction_patterns = agent_def.get("compaction_patterns", [])
    if compaction_patterns and any(p in tail for p in compaction_patterns):
        return {"state": "compacted", "details": "context compaction detected in output"}

    # Check for crash / error patterns
    crash_patterns = ["Traceback", "SIGTERM", "killed", "exited with"]
    api_exhausted_patterns = ["Extra usage is required", "rate limit", "quota exceeded", "billing"]
    if any(p in tail for p in crash_patterns):
        # Only if also at a shell prompt (agent CLI crashed, back to shell)
        if any(p in tail for p in ["$", "❯", "#"]):
            if warm_policy == "cold" and not _agent_has_active_tasks(agent_id):
                return {"state": "parked", "details": f"cold agent '{agent_id}' process exited, no active tasks"}
            return {"state": "dead", "details": "agent process crashed, at shell prompt"}
    # API quota exhaustion is not a crash — don't restart in a loop
    if any(p.lower() in tail.lower() for p in api_exhausted_patterns):
        if any(p in tail for p in ["$", "❯", "#"]):
            return {"state": "parked", "details": f"agent '{agent_id}' API quota exhausted, waiting for credits"}

    # Check busy vs idle
    busy = any(p in tail for p in agent_def.get("busy_patterns", []))
    idle = any(p in tail for p in agent_def.get("idle_patterns", []))

    if busy:
        return {"state": "busy", "details": "agent actively working"}

    if idle:
        return {"state": "idle", "details": "agent at prompt"}

    # Check heartbeat file — if agent wrote a recent heartbeat, it's busy (not stuck)
    heartbeat_path = STATE_DIR / f"{agent_id}-heartbeat"
    if heartbeat_path.exists():
        try:
            hb_text = heartbeat_path.read_text().strip()
            hb_time = datetime.fromisoformat(hb_text)
            hb_age = (datetime.now(timezone.utc) - hb_time).total_seconds()
            if hb_age < 120:  # heartbeat within 2 minutes = actively working
                return {"state": "busy", "details": f"heartbeat {int(hb_age)}s ago"}
        except (ValueError, OSError):
            pass

    # Check for stuck: compare current pane to last snapshot
    prev_state = load_agent_state(agent_id)
    prev_snapshot = prev_state.get("last_pane_hash")
    current_hash = hash(pane.strip())

    if prev_snapshot == current_hash:
        # Pane unchanged since last check
        unchanged_since = prev_state.get("unchanged_since")
        if unchanged_since:
            unchanged_dt = datetime.fromisoformat(unchanged_since)
            minutes_stuck = (datetime.now(timezone.utc) - unchanged_dt).total_seconds() / 60
            if minutes_stuck >= STUCK_THRESHOLD_MINUTES:
                return {"state": "stuck", "details": f"no output change for {int(minutes_stuck)} minutes"}
    else:
        # Pane changed, reset stuck timer
        state = load_agent_state(agent_id)
        state["last_pane_hash"] = current_hash
        state["unchanged_since"] = datetime.now(timezone.utc).isoformat()
        save_agent_state(agent_id, state)

    return {"state": "unknown", "details": "unable to determine state definitively"}


def _detect_openclaw_state(agent_id: str, agent_def: dict) -> dict:
    """Check OpenClaw agent state via session listing."""
    try:
        result = subprocess.run(
            ["openclaw", "sessions", "--agent", agent_id, "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"state": "dead", "details": f"openclaw session query failed: {result.stderr.strip()[:100]}"}

        payload = json.loads(result.stdout or "{}")
        sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
        if not sessions:
            if agent_def.get("warm_policy", "cold") == "cold" and not _agent_has_active_tasks(agent_id):
                return {"state": "parked", "details": "cold openclaw agent intentionally parked"}
            return {"state": "idle", "details": "no active openclaw sessions"}

        latest = min(sessions, key=lambda s: int(s.get("ageMs") or 10**18))
        age_minutes = int(latest.get("ageMs", 0)) / 60000

        if age_minutes > STUCK_THRESHOLD_MINUTES:
            # Cold agents with no active tasks are parked, not stuck
            if agent_def.get("warm_policy", "cold") == "cold" and not _agent_has_active_tasks(agent_id):
                return {"state": "parked", "details": f"cold agent '{agent_id}' idle for {int(age_minutes)}m, no active tasks"}
            return {"state": "stuck", "details": f"last activity {int(age_minutes)} minutes ago"}

        return {"state": "busy", "details": f"active session, last activity {int(age_minutes)}m ago"}
    except FileNotFoundError:
        return {"state": "unknown", "details": "openclaw CLI not found"}
    except Exception as e:
        return {"state": "unknown", "details": str(e)[:100]}


# ── Actions ───────────────────────────────────────────────────────────────────
def get_agent_context(agent_id: str) -> str:
    """Fetch current context for agent restart: tasks + recent memories."""
    parts: list[str] = []

    # Current tasks from Squad Service
    try:
        resp = requests.get(
            f"{SQUAD_URL}/tasks",
            params={"agent": agent_id, "status": "in_progress"},
            headers=_squad_headers(),
            timeout=5,
        )
        if resp.status_code == 200:
            tasks = resp.json()
            if isinstance(tasks, dict):
                tasks = tasks.get("tasks", [])
            for t in tasks[:3]:
                parts.append(f"IN-PROGRESS TASK: {t.get('title', 'unknown')} (id: {t.get('id', '?')[:8]})")
    except Exception:
        pass

    # Recent memories from Mirror
    try:
        resp = requests.post(
            f"{MIRROR_URL}/search",
            json={"query": f"{agent_id} task work current status", "agent_filter": agent_id, "limit": 3},
            timeout=5,
        )
        if resp.status_code == 200:
            results = resp.json()
            if isinstance(results, dict):
                results = results.get("results", [])
            for r in results[:3]:
                text = r.get("text", r.get("content", ""))[:200]
                if text:
                    parts.append(f"MEMORY: {text}")
    except Exception:
        pass

    # State file — rich snapshot from Gap 4
    state = load_agent_state(agent_id)
    if state.get("current_task"):
        parts.append(f"LAST KNOWN TASK: {state['current_task']}")
    if state.get("current_tasks"):
        for t in state["current_tasks"]:
            task_line = f"ACTIVE TASK: {t.get('title', 'unknown')} (id: {t.get('id', '?')[:8]})"
            if task_line not in parts:
                parts.append(task_line)
    if state.get("last_output_snippet"):
        parts.append(f"LAST OUTPUT:\n{state['last_output_snippet']}")
    if state.get("recent_bus_messages"):
        for msg in state["recent_bus_messages"][:2]:
            if msg and msg.strip():
                parts.append(f"BUS MESSAGE: {msg[:150]}")
    # Gap 8: Session persistence — include working directory and git state
    if state.get("working_directory"):
        parts.append(f"WORKING DIR: {state['working_directory']}")
    if state.get("git_branch"):
        parts.append(f"GIT BRANCH: {state['git_branch']}")

    # Gap 3: If state is thin, try recovering from Mirror compaction checkpoint
    if len(parts) <= 2:
        try:
            resp = requests.post(
                f"{MIRROR_URL}/search",
                json={"query": f"compaction-checkpoint {agent_id}", "limit": 1},
                timeout=5,
            )
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, dict):
                    results = results.get("results", [])
                for r in results[:1]:
                    text = r.get("text", r.get("content", ""))
                    if text and "compaction-checkpoint" in text:
                        parts.append(f"RECOVERED FROM MIRROR: {text[:500]}")
        except Exception:
            pass

    if not parts:
        return "Check your inbox for pending work."

    context = "\n".join(parts)
    if len(context) > 2048:
        context = context[:2048] + "\n[context truncated]"
    return context


def restart_tmux_agent(agent_id: str, agent_def: dict) -> bool:
    """Restart a dead tmux agent with context injection."""
    session = agent_def.get("session", agent_id)
    restart_cmd = agent_def.get("restart_cmd", "claude --continue")

    # Check if session exists but agent process is dead
    session_exists = check_tmux_alive(session)

    if not session_exists:
        # Create new tmux session
        try:
            working_dir = _get_agent_workdir(agent_id)
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", session, "-c", working_dir],
                capture_output=True, timeout=10,
            )
            time.sleep(1)
        except Exception as e:
            logger.error(f"Failed to create tmux session for {agent_id}: {e}")
            return False

    # Get context to inject
    context = get_agent_context(agent_id)

    # Start the agent process
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", session, restart_cmd, "Enter"],
            capture_output=True, timeout=5,
        )
        time.sleep(3)  # Wait for agent to initialize

        # Inject context as first message
        if context:
            context_msg = f"You were restarted by the lifecycle manager. Resume your work:\n{context}"
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "-l", context_msg],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "Enter"],
                capture_output=True, timeout=5,
            )

        # Gap 9: Auto-check inbox on restart
        time.sleep(5)  # Wait for agent to process context
        inbox_count = _count_unread_messages(agent_id)
        if inbox_count > 0:
            inbox_msg = f"You have {inbox_count} unread bus messages. Check your inbox."
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "-l", inbox_msg],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "Enter"],
                capture_output=True, timeout=5,
            )

        return True
    except Exception as e:
        logger.error(f"Failed to restart agent {agent_id}: {e}")
        return False


def _get_agent_workdir(agent_id: str) -> str:
    """Get the working directory for an agent from the registry."""
    all_agents = get_all_agents()
    agent = all_agents.get(agent_id)
    if agent and agent.workdir:
        return agent.workdir
    return str(Path.home())


def handle_stuck_agent(agent_id: str, agent_def: dict) -> bool:
    """Handle a stuck agent: send interrupt, then escalate."""
    state = load_agent_state(agent_id)
    stuck_count = state.get("stuck_interventions", 0)

    if stuck_count >= 3:
        # Escalate: too many stuck interventions
        alert_bus(agent_id, "stuck_escalated", f"Agent {agent_id} stuck {stuck_count} times, needs manual intervention")
        state["stuck_interventions"] = 0
        save_agent_state(agent_id, state)
        return False

    if agent_def.get("type") == "tmux":
        session = agent_def.get("session", agent_id)
        # Send Ctrl-C to interrupt, then re-inject task
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "C-c"],
                capture_output=True, timeout=5,
            )
            time.sleep(2)

            context = get_agent_context(agent_id)
            if context:
                msg = f"You appear stuck. Here's your current work:\n{context}"
                subprocess.run(
                    ["tmux", "send-keys", "-t", session, "-l", msg],
                    capture_output=True, timeout=5,
                )
                time.sleep(0.3)
                subprocess.run(
                    ["tmux", "send-keys", "-t", session, "Enter"],
                    capture_output=True, timeout=5,
                )
        except Exception as e:
            logger.error(f"Failed to unstick {agent_id}: {e}")
            return False

    state["stuck_interventions"] = stuck_count + 1
    save_agent_state(agent_id, state)
    return True


def _count_unread_messages(agent_id: str) -> int:
    """Count unread messages in an agent's bus inbox."""
    r = get_redis()
    if not r:
        return 0
    try:
        stream_key = f"sos:stream:global:agent:{agent_id}"
        info = r.xinfo_stream(stream_key)
        return info.get("length", 0)
    except Exception:
        return 0


def check_dead_letter_queue() -> list[dict]:
    """Gap 9: Find messages sitting unread for >1 hour, alert.

    Also redirects messages unread >10 minutes to mumega (orchestrator).
    """
    r = get_redis()
    if not r:
        return []

    alerts: list[dict] = []
    now_ms = int(time.time() * 1000)
    ten_min_ms = 10 * 60 * 1000
    one_hour_ms = 60 * 60 * 1000

    for agent_id in AGENT_DEFS:
        stream_key = f"sos:stream:global:agent:{agent_id}"
        try:
            # Check oldest unread message
            messages = r.xrange(stream_key, count=5)
            if not messages:
                continue

            for msg_id, msg_data in messages:
                # Extract timestamp from stream ID (format: timestamp-sequence)
                msg_ts = int(msg_id.split("-")[0])
                age_ms = now_ms - msg_ts

                if age_ms > one_hour_ms:
                    try:
                        preview_raw = msg_data.get("payload", "") if isinstance(msg_data, dict) else str(msg_data)
                        preview_parsed = json.loads(preview_raw) if isinstance(preview_raw, str) and preview_raw.startswith("{") else {}
                        preview = preview_parsed.get("text", preview_raw)[:100] if preview_parsed else str(preview_raw)[:100]
                    except Exception:
                        preview = str(msg_data)[:100]
                    alerts.append({
                        "agent": agent_id,
                        "msg_id": msg_id,
                        "age_minutes": int(age_ms / 60000),
                        "action": "dead_letter",
                        "preview": preview,
                    })
                elif age_ms > ten_min_ms:
                    # Check if agent is dead/offline
                    agent_def = AGENT_DEFS.get(agent_id, {})
                    if agent_def.get("type") == "tmux":
                        session = agent_def.get("session", agent_id)
                        if not check_tmux_alive(session):
                            # Agent offline, redirect to orchestrator
                            try:
                                msg_payload = msg_data.get("payload", "") if isinstance(msg_data, dict) else str(msg_data)
                                r.xadd(
                                    "sos:stream:global:agent:mumega",
                                    {
                                        "payload": json.dumps({
                                            "type": "dead_letter_redirect",
                                            "source": "lifecycle-manager",
                                            "original_agent": agent_id,
                                            "text": f"Message to {agent_id} unread for {int(age_ms/60000)}m (agent offline): {msg_payload[:200]}",
                                        }),
                                    },
                                    maxlen=500,
                                )
                            except Exception:
                                pass
        except Exception:
            continue

    if alerts:
        # Alert Discord about dead letters
        dead_letters = [a for a in alerts if a["action"] == "dead_letter"]
        if dead_letters:
            msg = f"Dead letter queue: {len(dead_letters)} messages unread >1h: " + ", ".join(
                f"{a['agent']} ({a['age_minutes']}m)" for a in dead_letters[:5]
            )
            alert_discord(msg)
            log_event("system", "dead_letters", msg)

    return alerts


def _snapshot_to_mirror(agent_id: str) -> bool:
    """Save working context to Mirror before compaction destroys it (Gap 3).

    Stores a compaction checkpoint engram so restarted agents can recover
    rich context even if the state file is stale.
    """
    context = get_agent_context(agent_id)
    if not context:
        return False

    try:
        payload = {
            "agent": agent_id,
            "text": f"[compaction-checkpoint] {context}",
            "series": "compaction-checkpoint",
            "project": "sos",
            "context_id": f"compact-{agent_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "metadata": {"event": "compaction", "agent": agent_id},
        }
        resp = requests.post(
            f"{MIRROR_URL}/store",
            json=payload,
            headers={"Authorization": f"Bearer sk-mumega-internal-001"},
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info(f"Compaction checkpoint saved to Mirror for {agent_id}")
            return True
        else:
            logger.warning(f"Mirror store failed for {agent_id}: {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to snapshot {agent_id} to Mirror: {e}")
    return False


def handle_compacted_agent(agent_id: str, agent_def: dict) -> bool:
    """Handle a compacted agent: snapshot to Mirror, then re-inject context."""
    if agent_def.get("type") != "tmux":
        return False

    # Gap 3: Save context to Mirror BEFORE re-injection
    _snapshot_to_mirror(agent_id)

    session = agent_def.get("session", agent_id)
    context = get_agent_context(agent_id)

    if context:
        try:
            msg = f"Context was compacted. Here's what you were working on:\n{context}"
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "-l", msg],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "Enter"],
                capture_output=True, timeout=5,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to re-inject context for {agent_id}: {e}")
    return False


# ── Alerting ──────────────────────────────────────────────────────────────────
def alert_bus(agent_id: str, event_type: str, message: str) -> None:
    """Send lifecycle alert via Redis bus."""
    r = get_redis()
    if not r:
        return
    try:
        payload = json.dumps({
            "type": f"lifecycle.{event_type}",
            "source": "lifecycle-manager",
            "agent": agent_id,
            "text": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Alert to mumega (orchestrator)
        r.xadd("sos:stream:global:agent:mumega", {"payload": payload}, maxlen=500)
        r.publish("sos:wake:mumega", payload)
        # Also to lifecycle stream
        r.xadd("sos:stream:lifecycle", {"payload": payload}, maxlen=1000)
    except Exception as e:
        logger.warning(f"Bus alert failed: {e}")


def alert_discord(message: str) -> None:
    """Send alert to Discord."""
    try:
        subprocess.run(
            ["bash", DISCORD_ALERT_SCRIPT, "system", "alerts", f"🔄 LIFECYCLE: {message}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception as e:
        logger.warning(f"Discord alert failed: {e}")


# ── Main Loop ─────────────────────────────────────────────────────────────────
def run_cycle(cycle_num: int) -> dict:
    """Run one lifecycle check cycle across all agents."""
    logger.info(f"=== Lifecycle cycle {cycle_num} ===")
    results: dict[str, dict] = {}

    for agent_id, agent_def in AGENT_DEFS.items():
        detection = detect_agent_state(agent_id, agent_def)
        state_val = detection["state"]
        details = detection["details"]
        results[agent_id] = detection

        if state_val == "dead":
            log_event(agent_id, "dead_detected", details)
            alert_bus(agent_id, "dead", f"Agent {agent_id} is dead: {details}")

            if agent_def.get("type") == "tmux":
                restarted = restart_tmux_agent(agent_id, agent_def)
                if restarted:
                    log_event(agent_id, "restarted", "auto-restarted with context")
                    alert_bus(agent_id, "restarted", f"Agent {agent_id} auto-restarted")
                else:
                    log_event(agent_id, "restart_failed", "auto-restart failed")
                    alert_discord(f"Agent {agent_id} dead and restart failed: {details}")

        elif state_val == "stuck":
            log_event(agent_id, "stuck_detected", details)
            handled = handle_stuck_agent(agent_id, agent_def)
            if handled:
                log_event(agent_id, "unstick_attempted", "sent interrupt + context re-injection")
            else:
                alert_discord(f"Agent {agent_id} stuck and escalated: {details}")

        elif state_val == "compacted":
            log_event(agent_id, "compaction_detected", details)
            handle_compacted_agent(agent_id, agent_def)
            log_event(agent_id, "context_reinjected", "re-injected task context after compaction")

        elif state_val in ("busy", "idle", "parked"):
            # Healthy states — save rich state snapshot (Gap 4 + Gap 8)
            agent_state = load_agent_state(agent_id)
            agent_state["last_seen_state"] = state_val
            agent_state["last_seen_at"] = datetime.now(timezone.utc).isoformat()
            if state_val == "busy":
                agent_state["stuck_interventions"] = 0  # Reset stuck counter
                # Always snapshot busy agents
                _snapshot_agent_context(agent_id, agent_state, agent_def)
            elif state_val == "idle" and cycle_num % 5 == 0:
                # Gap 8: Snapshot idle agents every 5 cycles (5 min)
                _snapshot_agent_context(agent_id, agent_state, agent_def)
            save_agent_state(agent_id, agent_state)

    # Prune stale cold workers every 10 cycles.
    if cycle_num % 10 == 0:
        pruned_workers = prune_stale_workers()
        if pruned_workers:
            results["_worker_teardown"] = {"count": len(pruned_workers), "workers": pruned_workers}
            log_event("system", "worker_teardown", f"pruned {len(pruned_workers)} stale worker(s)")

    # Gap 9: Dead letter queue check (every 5th cycle = every 5 min)
    if cycle_num % 5 == 0:
        dead_letters = check_dead_letter_queue()
        if dead_letters:
            results["_dead_letters"] = {"count": len(dead_letters)}

    # Summary
    states = {}
    for agent_id, detection in results.items():
        if agent_id.startswith("_"):
            continue
        s = detection["state"]
        states.setdefault(s, []).append(agent_id)

    summary_parts = [f"{state}: {', '.join(agents)}" for state, agents in states.items()]
    logger.info(f"Cycle {cycle_num} complete — {' | '.join(summary_parts)}")

    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Agent Lifecycle Manager")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--watch", action="store_true", help="Run continuously")
    args = parser.parse_args()

    logger.info(f"Agent Lifecycle Manager starting (poll every {POLL_INTERVAL}s, stuck threshold {STUCK_THRESHOLD_MINUTES}m)")

    if args.once:
        results = run_cycle(1)
        print(json.dumps(results, indent=2))
        return

    cycle_num = 0
    while True:
        cycle_num += 1
        try:
            run_cycle(cycle_num)
        except KeyboardInterrupt:
            logger.info("Lifecycle manager stopped.")
            break
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
