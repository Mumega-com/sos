"""
Cross-Agent Coordination Protocol — Gap #5 from HARNESS-GAPS.md

Implements DELEGATE/ACK/RESULT handshake on top of the bus + Squad Service.

Protocol:
  1. Manager calls delegate() → creates task in Squad, sends bus DELEGATE message
  2. Worker receives DELEGATE → calls ack() → claims task, sends bus ACK
  3. Worker finishes → calls report_result() → completes task, sends bus RESULT
  4. Manager receives RESULT → calls verify() → confirms completion

State machine (Squad Service tracks status):
  DELEGATE → task.queued → bus DELEGATE to worker
  ACK      → task.claimed → bus ACK to manager
  RESULT   → task.done → bus RESULT to manager
  VERIFY   → (manager confirms, optional)

Usage:
  coord = Coordinator(redis_url, squad_url)
  # Manager delegates
  task_id = await coord.delegate("worker", "Fix the pricing page", "kasra")
  # Worker acknowledges
  await coord.ack(task_id, "worker")
  # Worker reports
  await coord.report_result(task_id, "worker", "Fixed pricing. See /pricing")
  # Manager verifies
  await coord.verify(task_id, "kasra")
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import redis as redis_lib
import requests

logger = logging.getLogger("coordination")

try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".env.secrets")
except ImportError:
    pass

SQUAD_URL = os.environ.get("SQUAD_URL", "http://127.0.0.1:8060")
SQUAD_TOKEN = os.environ.get("SOS_SYSTEM_TOKEN", "")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_URL = os.environ.get(
    "REDIS_URL",
    f"redis://:{REDIS_PASSWORD}@localhost:6379/0" if REDIS_PASSWORD else "redis://localhost:6379/0",
)


class Coordinator:
    """Manages the DELEGATE/ACK/RESULT handshake between agents."""

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        squad_url: str = SQUAD_URL,
    ) -> None:
        self.redis = redis_lib.from_url(redis_url, decode_responses=True, socket_timeout=3)
        self.squad_url = squad_url
        self._squad_headers = {"Authorization": f"Bearer {SQUAD_TOKEN}"} if SQUAD_TOKEN else {}

    def _squad_request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Make an authenticated request to Squad Service."""
        url = f"{self.squad_url}{path}"
        kwargs.setdefault("timeout", 10)
        kwargs.setdefault("headers", {}).update(self._squad_headers)
        return getattr(requests, method)(url, **kwargs)

    def _send_bus(self, to: str, message: dict) -> None:
        """Send a coordination message via Redis bus."""
        stream_key = f"sos:stream:global:agent:{to}"
        self.redis.xadd(stream_key, {"data": json.dumps(message)}, maxlen=500)
        self.redis.publish(f"sos:wake:{to}", json.dumps(message))

    def delegate(
        self,
        to: str,
        title: str,
        from_agent: str,
        description: str = "",
        priority: str = "medium",
        squad_id: str = "default",
    ) -> str:
        """Manager delegates a task to a worker.

        Creates task in Squad Service, sends DELEGATE bus message.
        Returns task_id.
        """
        task_id = str(uuid.uuid4())[:8] + "-" + str(uuid.uuid4())[:4]

        # Create task in Squad Service
        try:
            resp = self._squad_request("post", "/tasks", json={
                "id": task_id,
                "squad_id": squad_id,
                "title": title,
                "description": description,
                "priority": priority,
                "assignee": to,
                "status": "queued",
                "labels": ["delegated", f"from:{from_agent}"],
            })
            if resp.status_code not in (200, 201):
                logger.warning(f"Squad task creation failed: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Squad Service unavailable: {e}")

        # Send DELEGATE message via bus
        self._send_bus(to, {
            "type": "DELEGATE",
            "source": from_agent,
            "task_id": task_id,
            "title": title,
            "description": description,
            "priority": priority,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": (
                f"DELEGATE task_id={task_id} from={from_agent}\n"
                f"Title: {title}\n"
                f"Priority: {priority}\n"
                f"{description}\n\n"
                f"Please ACK when you start, and RESULT when done."
            ),
        })

        # Emit coordination event
        self.redis.xadd("sos:stream:coordination", {
            "data": json.dumps({
                "event": "delegate",
                "task_id": task_id,
                "from": from_agent,
                "to": to,
                "title": title,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }),
        }, maxlen=1000)

        logger.info(f"DELEGATE {task_id}: {from_agent} → {to}: {title}")
        return task_id

    def ack(self, task_id: str, agent: str) -> bool:
        """Worker acknowledges a delegated task.

        Claims task in Squad Service, sends ACK to delegator.
        """
        # Claim in Squad Service
        try:
            resp = self._squad_request("post", f"/tasks/{task_id}/claim", json={"agent": agent})
            if resp.status_code not in (200, 201):
                logger.warning(f"Task claim failed: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Squad Service unavailable for claim: {e}")

        # Find the delegator from task labels
        delegator = self._get_delegator(task_id)

        if delegator:
            self._send_bus(delegator, {
                "type": "ACK",
                "source": agent,
                "task_id": task_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "text": f"ACK task_id={task_id} agent={agent} — starting work now.",
            })

        self.redis.xadd("sos:stream:coordination", {
            "data": json.dumps({
                "event": "ack",
                "task_id": task_id,
                "agent": agent,
                "delegator": delegator,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }),
        }, maxlen=1000)

        logger.info(f"ACK {task_id}: {agent} acknowledged")
        return True

    def report_result(
        self,
        task_id: str,
        agent: str,
        summary: str,
        verify: str = "",
        status: str = "completed",
    ) -> bool:
        """Worker reports task result.

        Completes task in Squad Service, sends RESULT to delegator.
        """
        result_data = {
            "summary": summary,
            "verify": verify,
            "agent": agent,
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Complete in Squad Service
        try:
            resp = self._squad_request("post", f"/tasks/{task_id}/complete", json={"result": result_data, "agent": agent})
            if resp.status_code not in (200, 201):
                logger.warning(f"Task completion failed: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Squad Service unavailable for completion: {e}")

        # Notify delegator
        delegator = self._get_delegator(task_id)
        if delegator:
            self._send_bus(delegator, {
                "type": "RESULT",
                "source": agent,
                "task_id": task_id,
                "status": status,
                "summary": summary,
                "verify": verify,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "text": (
                    f"RESULT task_id={task_id} status={status}\n"
                    f"SUMMARY: {summary}\n"
                    + (f"VERIFY: {verify}\n" if verify else "")
                ),
            })

        self.redis.xadd("sos:stream:coordination", {
            "data": json.dumps({
                "event": "result",
                "task_id": task_id,
                "agent": agent,
                "status": status,
                "summary": summary,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }),
        }, maxlen=1000)

        logger.info(f"RESULT {task_id}: {agent} → {status}: {summary[:80]}")
        return True

    def verify(self, task_id: str, verifier: str, verified: bool = True) -> bool:
        """Manager verifies a completed task."""
        self.redis.xadd("sos:stream:coordination", {
            "data": json.dumps({
                "event": "verified",
                "task_id": task_id,
                "verifier": verifier,
                "verified": verified,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }),
        }, maxlen=1000)

        logger.info(f"VERIFIED {task_id}: {'OK' if verified else 'REJECTED'} by {verifier}")
        return True

    def _get_delegator(self, task_id: str) -> Optional[str]:
        """Find who delegated a task by checking Squad Service labels."""
        try:
            resp = self._squad_request("get", f"/tasks/{task_id}", timeout=5)
            if resp.status_code == 200:
                task = resp.json()
                labels = task.get("labels", [])
                for label in labels:
                    if isinstance(label, str) and label.startswith("from:"):
                        return label[5:]
        except Exception:
            pass
        return None

    def get_handshake_status(self, task_id: str) -> dict:
        """Get the current coordination status for a task."""
        events: list[dict] = []
        try:
            messages = self.redis.xrange("sos:stream:coordination", count=100)
            for _, msg_data in messages:
                data = json.loads(msg_data.get("data", "{}"))
                if data.get("task_id") == task_id:
                    events.append(data)
        except Exception:
            pass

        has_delegate = any(e["event"] == "delegate" for e in events)
        has_ack = any(e["event"] == "ack" for e in events)
        has_result = any(e["event"] == "result" for e in events)
        has_verified = any(e["event"] == "verified" for e in events)

        if has_verified:
            phase = "verified"
        elif has_result:
            phase = "result_pending_verification"
        elif has_ack:
            phase = "in_progress"
        elif has_delegate:
            phase = "delegated_awaiting_ack"
        else:
            phase = "unknown"

        return {
            "task_id": task_id,
            "phase": phase,
            "events": events,
        }
