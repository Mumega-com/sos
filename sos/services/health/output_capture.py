#!/usr/bin/env python3
"""
Output Capture Logger — Gap #2 from HARNESS-GAPS.md

Captures tmux pane content every 60 seconds, diffs against last capture,
stores changes, and parses for structured output (RESULT:, DONE:, ERROR:).

Structured output is forwarded to:
  - Mirror (memory storage)
  - Squad Service (task completion)
  - Redis lifecycle stream

Logs stored at ~/.sos/logs/{agent}/{date}.log

Run as:
  systemctl --user start output-capture.service
  python3 -m sos.services.health.output_capture --once
  python3 -m sos.services.health.output_capture --watch
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sos.kernel.agent_registry import get_capture_agents

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [output-capture] %(levelname)s %(message)s",
)
logger = logging.getLogger("output-capture")

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.environ.get("OUTPUT_CAPTURE_INTERVAL", "60"))
from sos.kernel.settings import get_settings as _get_settings
_oc_settings = _get_settings()
SQUAD_URL = _oc_settings.services.squad_url
MIRROR_URL = _oc_settings.services.mirror
REDIS_PASSWORD = _oc_settings.redis.password_str
REDIS_URL = _oc_settings.redis.resolved_url
SQUAD_TOKEN = _oc_settings.auth.system_token_str
LOG_BASE = Path.home() / ".sos" / "logs"
CAPTURE_LINES = 200  # Lines to capture per pane

LOG_BASE.mkdir(parents=True, exist_ok=True)


def _squad_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SQUAD_TOKEN}"} if SQUAD_TOKEN else {}


# Agents to capture (tmux only — OpenClaw agents log via their own system)
CAPTURE_AGENTS: dict[str, str] = get_capture_agents()

# Structured output patterns (Gap #3 integration)
RESULT_PATTERN = re.compile(
    r"^RESULT:\s*task_id=(\S+)\s+status=(\S+)",
    re.MULTILINE,
)
SUMMARY_PATTERN = re.compile(
    r"^SUMMARY:\s*(.+)$",
    re.MULTILINE,
)
VERIFY_PATTERN = re.compile(
    r"^VERIFY:\s*(.+)$",
    re.MULTILINE,
)
DONE_PATTERN = re.compile(
    r"^DONE:\s*(.+)$",
    re.MULTILINE,
)
ERROR_PATTERN = re.compile(
    r"^ERROR:\s*(.+)$",
    re.MULTILINE,
)


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


# ── Previous capture storage ──────────────────────────────────────────────────
_prev_captures: dict[str, str] = {}


def capture_pane(session: str) -> str:
    """Capture tmux pane content."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p", "-S", f"-{CAPTURE_LINES}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def compute_diff(agent_id: str, current: str) -> str:
    """Compute new lines since last capture."""
    prev = _prev_captures.get(agent_id, "")
    _prev_captures[agent_id] = current

    if not prev:
        return ""  # First capture, no diff

    prev_lines = prev.strip().split("\n")
    curr_lines = current.strip().split("\n")

    if prev_lines == curr_lines:
        return ""  # No change

    # Find where old content ends in new content
    # Simple approach: find the last line of prev in curr, take everything after
    if prev_lines:
        last_prev = prev_lines[-1].strip()
        for i in range(len(curr_lines) - 1, -1, -1):
            if curr_lines[i].strip() == last_prev:
                new_lines = curr_lines[i + 1:]
                return "\n".join(new_lines)

    # Fallback: return last N lines that differ
    new_content = []
    for i, line in enumerate(curr_lines):
        if i >= len(prev_lines) or line != prev_lines[i]:
            new_content.append(line)

    return "\n".join(new_content[-50:])  # Cap at 50 new lines


def store_log(agent_id: str, diff: str) -> None:
    """Store diff to agent log file."""
    agent_log_dir = LOG_BASE / agent_id
    agent_log_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = agent_log_dir / f"{date_str}.log"

    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    with open(log_file, "a") as f:
        f.write(f"\n--- {timestamp} ---\n")
        f.write(diff)
        f.write("\n")


def parse_structured_output(agent_id: str, diff: str) -> list[dict]:
    """Parse structured RESULT:, DONE:, ERROR: patterns from output."""
    events: list[dict] = []

    # RESULT: task_id=xxx status=completed
    for match in RESULT_PATTERN.finditer(diff):
        task_id = match.group(1)
        status = match.group(2)
        summary = ""
        verify = ""

        # Look for associated SUMMARY: and VERIFY:
        summary_match = SUMMARY_PATTERN.search(diff)
        if summary_match:
            summary = summary_match.group(1)
        verify_match = VERIFY_PATTERN.search(diff)
        if verify_match:
            verify = verify_match.group(1)

        events.append({
            "type": "task_result",
            "agent": agent_id,
            "task_id": task_id,
            "status": status,
            "summary": summary,
            "verify": verify,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # DONE: <message>
    for match in DONE_PATTERN.finditer(diff):
        events.append({
            "type": "done",
            "agent": agent_id,
            "message": match.group(1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ERROR: <message>
    for match in ERROR_PATTERN.finditer(diff):
        events.append({
            "type": "error",
            "agent": agent_id,
            "message": match.group(1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return events


def forward_to_squad(event: dict) -> bool:
    """Forward task completion to Squad Service."""
    if event["type"] != "task_result":
        return False

    task_id = event["task_id"]
    status = event["status"]
    summary = event.get("summary", "")

    try:
        resp = requests.post(
            f"{SQUAD_URL}/tasks/{task_id}/complete",
            json={"result": summary, "status": status, "agent": event["agent"]},
            headers=_squad_headers(),
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info(f"Forwarded task result to Squad: {task_id} → {status}")
            return True
        logger.warning(f"Squad rejected task result: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"Failed to forward to Squad: {e}")
    return False


def forward_to_mirror(agent_id: str, event: dict) -> bool:
    """Store structured output in Mirror as memory."""
    text = f"Agent {agent_id} "
    if event["type"] == "task_result":
        text += f"completed task {event['task_id']}: {event.get('summary', 'no summary')}"
    elif event["type"] == "done":
        text += f"reported done: {event['message']}"
    elif event["type"] == "error":
        text += f"reported error: {event['message']}"

    try:
        resp = requests.post(
            f"{MIRROR_URL}/store",
            json={
                "text": text,
                "agent": agent_id,
                "tags": [event["type"], "output_capture"],
            },
            timeout=10,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def _run_verification(event: dict) -> None:
    """Gap 6: Run auto-verification for a task result with VERIFY: line."""
    try:
        import asyncio
        from sos.kernel.verification import verify_action, parse_verify_line

        verify_text = event.get("verify", "")
        if not verify_text:
            return

        method, target, match = parse_verify_line(verify_text)
        result = asyncio.run(verify_action(
            method=method,
            target=target,
            match=match,
            task_id=event.get("task_id", ""),
            agent=event.get("agent", ""),
        ))

        status = "PASS" if result["verified"] else "FAIL"
        logger.info(f"Verification {status}: {method} on {target[:60]}")
    except Exception as e:
        logger.warning(f"Verification failed: {e}")


def forward_to_redis(event: dict) -> None:
    """Publish structured output to Redis stream."""
    r = get_redis()
    if not r:
        return
    try:
        r.xadd(
            "sos:stream:output_capture",
            {"data": json.dumps(event)},
            maxlen=5000,
        )
        # Also publish to wake channel for real-time consumers
        if event["type"] == "task_result":
            r.publish("sos:events:task.completed", json.dumps(event))
    except Exception as e:
        logger.warning(f"Redis publish failed: {e}")


# ── Main Loop ─────────────────────────────────────────────────────────────────
def run_cycle(cycle_num: int) -> dict:
    """Run one capture cycle across all agents."""
    results: dict[str, dict] = {}

    for agent_id, session in CAPTURE_AGENTS.items():
        pane = capture_pane(session)
        if not pane.strip():
            results[agent_id] = {"captured": False, "reason": "empty pane or no session"}
            continue

        diff = compute_diff(agent_id, pane)
        if not diff.strip():
            results[agent_id] = {"captured": True, "new_lines": 0}
            continue

        # Store the diff
        store_log(agent_id, diff)
        line_count = len(diff.strip().split("\n"))
        results[agent_id] = {"captured": True, "new_lines": line_count}

        # Parse structured output
        events = parse_structured_output(agent_id, diff)
        if events:
            results[agent_id]["structured_events"] = len(events)
            for event in events:
                logger.info(f"Structured output from {agent_id}: {event['type']} — {json.dumps(event)[:200]}")
                forward_to_redis(event)
                forward_to_mirror(agent_id, event)
                if event["type"] == "task_result":
                    forward_to_squad(event)
                    # Gap 6: Auto-verify if VERIFY: line present
                    if event.get("verify"):
                        _run_verification(event)

    # Summary
    active = sum(1 for r in results.values() if r.get("new_lines", 0) > 0)
    total_lines = sum(r.get("new_lines", 0) for r in results.values())
    total_events = sum(r.get("structured_events", 0) for r in results.values())

    if active > 0 or total_events > 0:
        logger.info(
            f"Cycle {cycle_num}: {active} agents active, "
            f"{total_lines} new lines, {total_events} structured events"
        )

    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Output Capture Logger")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--watch", action="store_true", help="Run continuously")
    args = parser.parse_args()

    logger.info(f"Output Capture Logger starting (poll every {POLL_INTERVAL}s)")

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
            logger.info("Output capture stopped.")
            break
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
