#!/usr/bin/env python3
"""
Task Polling Daemon — Gap #10 from HARNESS-GAPS.md

Makes agents autonomous: they pick up assigned tasks without human intervention.

Two modes:
  1. Poll: Every 5 minutes, query Squad Service for assigned/queued tasks per agent
  2. Event: Subscribe to Redis task.assigned events for real-time delivery

When a new task is found:
  - Check if agent is idle (reuses lifecycle detection)
  - Inject task prompt into tmux session or OpenClaw wake channel
  - Mark task as claimed in Squad Service

Run as:
  systemctl --user start task-poller.service
  python3 -m sos.services.health.task_poller --once
  python3 -m sos.services.health.task_poller --watch
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import threading
from datetime import datetime, timedelta, timezone
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

from sos.kernel.agent_registry import get_executor_agents, get_agent, is_coordinator, AgentType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [task-poller] %(levelname)s %(message)s",
)
logger = logging.getLogger("task-poller")

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.environ.get("TASK_POLL_INTERVAL", "300"))  # 5 minutes
SQUAD_URL = os.environ.get("SQUAD_URL", "http://127.0.0.1:8060")
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_URL = os.environ.get("REDIS_URL", f"redis://:{REDIS_PASSWORD}@localhost:6379/0" if REDIS_PASSWORD else "redis://localhost:6379/0")
SQUAD_TOKEN = os.environ.get("SOS_SYSTEM_TOKEN", "")
STATE_DIR = Path.home() / ".sos" / "state"
LOG_DIR = Path.home() / ".sos" / "logs" / "task-poller"

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _squad_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SQUAD_TOKEN}"} if SQUAD_TOKEN else {}


def _build_agent_routing() -> dict[str, dict]:
    result = {}
    for name, agent in get_executor_agents().items():
        entry = {"type": agent.type.value}
        if agent.type == AgentType.TMUX:
            entry["session"] = agent.session
        result[name] = entry
    return result

AGENT_ROUTING = _build_agent_routing()

# Max task age in hours — ignore tasks older than this
MAX_TASK_AGE_HOURS = int(os.environ.get("TASK_MAX_AGE_HOURS", "24"))

# Track delivered tasks to avoid double-delivery (persisted to disk)
DELIVERED_TASKS_FILE = STATE_DIR / "delivered_tasks.json"


def _load_delivered_tasks() -> set[str]:
    """Load delivered task IDs from disk (survives restart)."""
    if DELIVERED_TASKS_FILE.exists():
        try:
            data = json.loads(DELIVERED_TASKS_FILE.read_text())
            return set(data.get("task_ids", []))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_delivered_tasks(task_ids: set[str]) -> None:
    """Persist delivered task IDs to disk."""
    # Keep last 1000
    ids = list(task_ids)[-1000:]
    DELIVERED_TASKS_FILE.write_text(json.dumps({"task_ids": ids, "updated_at": datetime.now(timezone.utc).isoformat()}))


_delivered_tasks: set[str] = _load_delivered_tasks()


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


# ── Agent State Checks ────────────────────────────────────────────────────────
def is_tmux_idle(session: str) -> bool:
    """Check if a tmux agent is idle (at prompt)."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            return False

        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p", "-S", "-5"],
            capture_output=True, text=True, timeout=5,
        )
        tail = result.stdout.strip()
        idle_patterns = ["❯", "$ ", "›", "Type your message", "● YOLO", "Use /skills"]
        busy_patterns = ["Thinking", "Writing", "Generating", "Transmuting", "Churning", "Running"]

        has_idle = any(p in tail for p in idle_patterns)
        has_busy = any(p in tail for p in busy_patterns)
        return has_idle and not has_busy
    except Exception:
        return False


# ── Task Fetching ─────────────────────────────────────────────────────────────
def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def fetch_assigned_tasks(agent_id: str) -> list[dict]:
    """Fetch tasks assigned to an agent that are fresh and actionable."""
    if is_coordinator(agent_id):
        return []
    tasks: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_TASK_AGE_HOURS)

    # Only fetch queued tasks (not backlog/claimed which are often stale)
    try:
        resp = requests.get(
            f"{SQUAD_URL}/tasks",
            params={"assignee": agent_id, "status": "queued"},
            headers=_squad_headers(),
            timeout=5,
        )
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, dict):
                payload = payload.get("tasks", [])
            if isinstance(payload, list):
                tasks.extend(payload)
    except Exception as e:
        logger.warning(f"Failed to fetch tasks for {agent_id}: {e}")

    # Filter: not already delivered, and not older than cutoff
    filtered: list[dict] = []
    for t in tasks:
        task_id = t.get("id", "")
        if task_id in _delivered_tasks:
            continue
        labels = t.get("labels", [])
        if not _task_is_coordinator_routed(labels):
            logger.info(
                "Skipping task %s for %s: no coordinator routing label",
                task_id[:8],
                agent_id,
            )
            continue
        created = _parse_iso(t.get("created_at") or t.get("updated_at"))
        if created and created < cutoff:
            continue
        filtered.append(t)

    # Sort by priority
    prio = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    filtered.sort(key=lambda t: prio.get(t.get("priority", "low"), 4))

    return filtered


# ── Task Delivery ─────────────────────────────────────────────────────────────
def deliver_task(agent_id: str, task: dict) -> bool:
    """Deliver a task to an agent's tmux session or OpenClaw."""
    if is_coordinator(agent_id):
        logger.warning(f"Refusing to auto-deliver task to coordinator {agent_id}")
        return False

    labels = task.get("labels", [])
    if not _task_is_coordinator_routed(labels):
        logger.warning(
            "Refusing to auto-deliver unrouted task %s to %s",
            task.get("id", "unknown")[:8],
            agent_id,
        )
        return False

    routing = AGENT_ROUTING.get(agent_id)
    if not routing:
        logger.warning(f"No routing for agent {agent_id}")
        return False

    task_id = task.get("id", "unknown")
    title = task.get("title", "Untitled task")
    description = task.get("description", "")
    priority = task.get("priority", "medium")

    prompt = (
        f"NEW TASK [{priority.upper()}] (id: {task_id[:8]}):\n"
        f"{title}\n\n"
        f"{description}\n\n"
        f"When done, output:\n"
        f"RESULT: task_id={task_id} status=completed\n"
        f"SUMMARY: <what you did>\n"
        f"VERIFY: <how to verify>"
    )

    if routing["type"] == "tmux":
        session = routing.get("session", agent_id)

        # Check if agent is idle first
        if not is_tmux_idle(session):
            logger.debug(f"{agent_id} is busy, deferring task delivery")
            return False

        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "-l", prompt],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "Enter"],
                capture_output=True, timeout=5,
            )
            logger.info(f"Delivered task {task_id[:8]} to {agent_id} (tmux)")
        except Exception as e:
            logger.error(f"tmux delivery failed for {agent_id}: {e}")
            return False

    elif routing["type"] == "openclaw":
        r = get_redis()
        if not r:
            return False
        try:
            r.xadd(
                f"sos:stream:global:agent:{agent_id}",
                {
                    "type": "task_dispatch",
                    "source": "task-poller",
                    "data": json.dumps({
                        "task_id": task_id,
                        "title": title,
                        "priority": priority,
                        "description": description,
                    }),
                },
            )
            r.publish(f"sos:wake:{agent_id}", json.dumps({
                "type": "task_dispatch",
                "text": prompt,
                "source": "task-poller",
            }))
            logger.info(f"Delivered task {task_id[:8]} to {agent_id} (openclaw)")
        except Exception as e:
            logger.error(f"OpenClaw delivery failed for {agent_id}: {e}")
            return False

    # Claim the task in Squad Service
    try:
        requests.post(
            f"{SQUAD_URL}/tasks/{task_id}/claim",
            json={"agent": agent_id},
            headers=_squad_headers(),
            timeout=5,
        )
    except Exception:
        pass

    # Track delivery (persisted to disk)
    _delivered_tasks.add(task_id)
    _save_delivered_tasks(_delivered_tasks)

    # Log
    log_entry = {
        "agent": agent_id,
        "task_id": task_id,
        "title": title,
        "priority": priority,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    log_file = LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return True


def _task_is_coordinator_routed(labels: object) -> bool:
    if not isinstance(labels, list):
        return False
    return any(
        isinstance(label, str) and (label.startswith("from:") or label.startswith("delegated-by:"))
        for label in labels
    )


# ── Event Listener (real-time) ────────────────────────────────────────────────
def start_event_listener() -> None:
    """Subscribe to task.assigned events for real-time delivery.

    Runs in a background thread alongside the poll loop.
    """
    def _listen() -> None:
        backoff = 5
        while True:
            try:
                r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=30)
                r.ping()  # Verify auth works before subscribing
                pubsub = r.pubsub()
                pubsub.subscribe("sos:events:task.assigned")
                logger.info("Event listener subscribed to task.assigned")
                backoff = 5  # Reset backoff on successful connection

                for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                        agent_id = data.get("agent") or data.get("assigned_to")
                        task_id = data.get("task_id")

                        if not agent_id or not task_id:
                            continue
                        if is_coordinator(agent_id):
                            continue
                        if task_id in _delivered_tasks:
                            continue

                        logger.info(f"Event: task {task_id[:8]} assigned to {agent_id}")

                        # Fetch full task from Squad
                        try:
                            resp = requests.get(f"{SQUAD_URL}/tasks/{task_id}", headers=_squad_headers(), timeout=5)
                            if resp.status_code == 200:
                                task = resp.json()
                                deliver_task(agent_id, task)
                        except Exception:
                            pass

                    except (json.JSONDecodeError, KeyError):
                        continue

            except redis_lib.exceptions.AuthenticationError as e:
                logger.error(f"Event listener auth failed (check REDIS_PASSWORD): {e}")
                time.sleep(60)  # Long wait on auth failure
            except Exception as e:
                logger.warning(f"Event listener error, reconnecting in {backoff}s: {e}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)  # Exponential backoff, max 60s

    thread = threading.Thread(target=_listen, daemon=True)
    thread.start()


# ── Main Loop ─────────────────────────────────────────────────────────────────
def run_cycle(cycle_num: int) -> dict:
    """Poll for assigned tasks and deliver to idle agents."""
    results: dict[str, dict] = {}

    for agent_id in AGENT_ROUTING:
        tasks = fetch_assigned_tasks(agent_id)
        if not tasks:
            results[agent_id] = {"pending_tasks": 0}
            continue

        results[agent_id] = {"pending_tasks": len(tasks)}

        # Deliver first task (one at a time per agent)
        task = tasks[0]
        delivered = deliver_task(agent_id, task)
        results[agent_id]["delivered"] = delivered
        if delivered:
            results[agent_id]["delivered_task"] = task.get("id", "")[:8]

    # Summary
    delivered_count = sum(1 for r in results.values() if r.get("delivered"))
    pending_total = sum(r.get("pending_tasks", 0) for r in results.values())

    if delivered_count > 0 or pending_total > 0:
        logger.info(
            f"Cycle {cycle_num}: {delivered_count} tasks delivered, "
            f"{pending_total} total pending across all agents"
        )

    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Task Polling Daemon")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--watch", action="store_true", help="Run continuously")
    parser.add_argument("--no-events", action="store_true", help="Disable event listener")
    args = parser.parse_args()

    logger.info(f"Task Polling Daemon starting (poll every {POLL_INTERVAL}s)")

    if args.once:
        results = run_cycle(1)
        print(json.dumps(results, indent=2))
        return

    # Start event listener in background
    if not args.no_events:
        start_event_listener()

    cycle_num = 0
    while True:
        cycle_num += 1
        try:
            run_cycle(cycle_num)
        except KeyboardInterrupt:
            logger.info("Task poller stopped.")
            break
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
