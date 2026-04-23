#!/usr/bin/env python3
"""
Cortex Events — Event-driven brain wakeup

Subscribes to Redis pub/sub channels and triggers a brain cycle
immediately when something meaningful happens, instead of waiting
for the 2-hour cron fallback.

Events consumed:
  sos:channel:squad:*   — any squad channel message (pattern)
  task.completed        — task finished, schedule next work
  task.failed           — something broke, may need escalation
  task.blocked          — dependency issue, find alternative
  budget.exhausted      — squad ran out of budget
  sos:wake:brain        — explicit wake signal from any agent

Debounce: max one brain cycle per DEBOUNCE_SECONDS (default 60).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import redis

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CORTEX-EVENTS] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("cortex_events")

# ---------------------------------------------------------------------------
# sys.path — ensure kernel/ is importable regardless of cwd
# ---------------------------------------------------------------------------

import sys, os as _os
_SOVEREIGN_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _SOVEREIGN_DIR not in sys.path:
    sys.path.insert(0, _SOVEREIGN_DIR)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

from kernel.config import REDIS_URL as _KERNEL_REDIS_URL, REDIS_PASSWORD as _KERNEL_REDIS_PASSWORD

# Allow runtime override via env; fall back to kernel config
REDIS_PASSWORD: str = os.environ.get("REDIS_PASSWORD", _KERNEL_REDIS_PASSWORD)
DEBOUNCE_SECONDS: int = int(os.environ.get("BRAIN_DEBOUNCE_SECONDS", "60"))

# Patterns / channels to subscribe
PATTERN_SUBSCRIPTIONS: list[str] = [
    "sos:channel:squad:*",
]
CHANNEL_SUBSCRIPTIONS: list[str] = [
    "task.completed",
    "task.failed",
    "task.blocked",
    "budget.exhausted",
    "sos:wake:brain",
]

# ---------------------------------------------------------------------------
# State (process-level, not persisted — good enough for debounce)
# ---------------------------------------------------------------------------

_last_wake: float = 0.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redis_url() -> str:
    if REDIS_PASSWORD:
        return f"redis://:{REDIS_PASSWORD}@localhost:6379/0"
    return _KERNEL_REDIS_URL


def parse_event(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract a structured event dict from a raw pub/sub message."""
    channel: str = msg.get("channel") or msg.get("pattern") or ""
    raw_data: str | bytes = msg.get("data", "")

    payload: dict[str, Any] = {}
    if isinstance(raw_data, str) and raw_data:
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            payload = {"raw": raw_data}

    return {
        "channel": channel,
        "type": msg.get("type", ""),
        "payload": payload,
        "ts": time.time(),
    }


def get_portfolio_state() -> str:
    """
    Get current portfolio state.

    Tries cortex.py snapshot first; falls back to brain.py hippocampus_recall()
    if cortex.py is not present.
    """
    cortex_script = Path(_SOVEREIGN_DIR) / "cortex.py"
    if cortex_script.exists():
        import subprocess

        try:
            result = subprocess.run(
                [sys.executable, str(cortex_script), "snapshot"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=_SOVEREIGN_DIR,
            )
            if result.returncode == 0 and result.stdout.strip():
                logger.info("Got portfolio state from cortex.py snapshot")
                return result.stdout.strip()
            if result.stderr:
                logger.warning("cortex.py snapshot stderr: %s", result.stderr[:200])
        except Exception as exc:
            logger.warning("cortex.py snapshot failed: %s", exc)

    # Fallback: import brain directly and use hippocampus_recall
    logger.info("Falling back to brain.hippocampus_recall()")
    try:
        sovereign_dir = _SOVEREIGN_DIR
        if sovereign_dir not in sys.path:
            sys.path.insert(0, sovereign_dir)

        import brain  # type: ignore[import]

        return brain.hippocampus_recall()
    except Exception as exc:
        logger.error("hippocampus_recall() failed: %s", exc)
        return f"[state unavailable: {exc}]"


def trigger_cycle(event: dict[str, Any]) -> None:
    """Run one full brain cycle in response to an event."""
    global _last_wake

    channel = event.get("channel", "?")
    logger.info("Brain cycle triggered by: %s", channel)

    try:
        sovereign_dir = _SOVEREIGN_DIR
        if sovereign_dir not in sys.path:
            sys.path.insert(0, sovereign_dir)

        import brain  # type: ignore[import]

        # 1. Get current portfolio state
        logger.info("Step 1/4 — gathering portfolio state")
        context = get_portfolio_state()

        # Annotate context with the triggering event so the brain has signal
        event_note = (
            f"\n\nTRIGGER EVENT:\n"
            f"  channel : {channel}\n"
            f"  payload : {json.dumps(event.get('payload', {}))[:300]}\n"
        )
        full_context = context + event_note

        # 2. Prefrontal think
        logger.info("Step 2/4 — prefrontal_think()")
        raw_decision = brain.prefrontal_think(full_context)
        logger.info("Decision (raw): %s", raw_decision[:200])

        # 3. Parse decision
        action: dict[str, Any]
        try:
            action = json.loads(raw_decision)
        except json.JSONDecodeError:
            # Try to extract JSON from surrounding text
            try:
                start = raw_decision.index("{")
                end = raw_decision.rindex("}") + 1
                action = json.loads(raw_decision[start:end])
            except (ValueError, json.JSONDecodeError):
                logger.error("Could not parse decision JSON — using fallback action")
                action = {
                    "action": f"Event-driven health check (parse failure on {channel})",
                    "goal_id": "maintenance",
                    "agent": "system",
                    "method": "health_check",
                    "details": "Triggered by event but decision was not parseable",
                    "expected_progress": 0.01,
                    "risk": 0.0,
                }

        logger.info(
            "Action: %s | Agent: %s | Method: %s",
            action.get("action", "?"),
            action.get("agent", "?"),
            action.get("method", "?"),
        )

        # 4. Motor execute
        logger.info("Step 3/4 — motor_execute()")
        result = brain.motor_execute(action)
        logger.info("Result: %s", result)

        # 5. Remember + report
        logger.info("Step 4/4 — remember + report")
        brain.remember(action, result)
        brain.report_to_discord(action, result)

        logger.info(
            "Brain cycle complete. success=%s | result=%s",
            result.get("success"),
            str(result.get("result", ""))[:120],
        )

    except Exception as exc:
        logger.exception("Brain cycle failed: %s", exc)


# ---------------------------------------------------------------------------
# Main listener loop
# ---------------------------------------------------------------------------


def listen() -> None:
    """Connect to Redis and process events indefinitely."""
    global _last_wake

    logger.info(
        "Cortex Events starting. debounce=%ds | redis=%s",
        DEBOUNCE_SECONDS,
        _redis_url().replace(REDIS_PASSWORD, "***") if REDIS_PASSWORD else "redis://localhost:6379/0",
    )

    r = redis.from_url(_redis_url(), decode_responses=True)

    # Verify connection
    try:
        r.ping()
        logger.info("Redis connection OK")
    except redis.exceptions.ConnectionError as exc:
        logger.error("Cannot connect to Redis: %s", exc)
        sys.exit(1)

    pubsub = r.pubsub()

    # Pattern subscriptions (squad channels)
    for pattern in PATTERN_SUBSCRIPTIONS:
        pubsub.psubscribe(pattern)
        logger.info("psubscribe: %s", pattern)

    # Exact channel subscriptions
    for channel in CHANNEL_SUBSCRIPTIONS:
        pubsub.subscribe(channel)
        logger.info("subscribe: %s", channel)

    logger.info("Listening for events...")

    for msg in pubsub.listen():
        msg_type: str = msg.get("type", "")

        # Only process actual messages, not subscribe confirmations
        if msg_type not in ("message", "pmessage"):
            continue

        now = time.time()
        elapsed = now - _last_wake

        if elapsed < DEBOUNCE_SECONDS:
            remaining = DEBOUNCE_SECONDS - elapsed
            logger.debug(
                "Debounce active — ignoring event on %s (%.0fs remaining)",
                msg.get("channel", "?"),
                remaining,
            )
            continue

        # Commit the wake timestamp before doing anything async
        _last_wake = now

        event = parse_event(msg)
        logger.info(
            "Event received: channel=%s type=%s",
            event["channel"],
            event["type"],
        )

        trigger_cycle(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Load secrets if running directly
    try:
        from dotenv import load_dotenv  # type: ignore[import]

        load_dotenv("/home/mumega/.env.secrets")
        load_dotenv("/home/mumega/therealmofpatterns/.env")
    except ImportError:
        pass  # dotenv optional — systemd sets env vars directly

    listen()


if __name__ == "__main__":
    main()
