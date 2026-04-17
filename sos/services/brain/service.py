"""BrainService — async bus consumer for scoring and dispatch.

Sprint 1 of Stage 2 (coherence plan). This module wires the event loop and
checkpoint machinery. Handlers are stubs that log + count only; real dispatch
and scoring land in Sprint 3.

Follows the canonical bus consumer pattern in
docs/architecture/MIRROR_BUS_CONSUMER_PATTERN.md (5 invariants).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis

from sos.services.brain.state import BrainState

logger = logging.getLogger("sos.brain")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Key pattern: sos:consumer:brain:checkpoint:<stream_name>
_CHECKPOINT_KEY_PREFIX = "sos:consumer:brain:checkpoint"

# XREAD blocking timeout in milliseconds
_BLOCK_MS = 1000

# How many seen message_ids to keep for idempotency (LRU cap)
_LRU_CAPACITY = 10_000

# Streams the Brain cares about
DEFAULT_STREAM_PATTERNS: list[str] = [
    "sos:stream:global:squad:*",
    "sos:stream:global:registry",
    "sos:stream:global:agent:*",
]

# Message types this service actively handles
_BRAIN_HANDLED_TYPES: frozenset[str] = frozenset(
    {
        "task.created",
        "task.completed",
        "task.failed",
        "task.routed",
        "agent_joined",
    }
)


# ---------------------------------------------------------------------------
# Small LRU for idempotency
# ---------------------------------------------------------------------------


class _LRUSet:
    """Bounded set that evicts the oldest entry when at capacity."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._store: OrderedDict[str, None] = OrderedDict()

    def __contains__(self, item: str) -> bool:
        return item in self._store

    def add(self, item: str) -> None:
        if item in self._store:
            self._store.move_to_end(item)
            return
        self._store[item] = None
        if len(self._store) > self._capacity:
            self._store.popitem(last=False)


# ---------------------------------------------------------------------------
# Redis client factory
# ---------------------------------------------------------------------------


def _build_redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "")
    if password:
        return f"redis://:{password}@{host}:{port}"
    return f"redis://{host}:{port}"


# ---------------------------------------------------------------------------
# BrainService
# ---------------------------------------------------------------------------


class BrainService:
    """Event-driven scoring and dispatch for SOS work queue.

    Subscribes to sos:stream:global:squad:*, sos:stream:global:registry,
    and sos:stream:global:agent:* via Redis XREAD. Checkpoints per stream.
    Idempotent on message_id. Fail-open on handler exceptions.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        stream_patterns: list[str] | None = None,
        consumer_name: str = "brain",
        redis_client: Optional[aioredis.Redis] = None,  # injectable for tests
    ) -> None:
        self._redis_url = redis_url or _build_redis_url()
        self._stream_patterns = stream_patterns or DEFAULT_STREAM_PATTERNS
        self._consumer_name = consumer_name

        # Injected redis client takes precedence (used in tests via fakeredis)
        self._redis: Optional[aioredis.Redis] = redis_client

        self._checkpoints: dict[str, str] = {}
        self._seen_ids: _LRUSet = _LRUSet(_LRU_CAPACITY)
        self._running = False
        self._stop_event: asyncio.Event = asyncio.Event()

        # Observable state
        self.state: BrainState = BrainState()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop. Runs until .stop() is called."""
        self._running = True
        self._stop_event.clear()
        logger.info(
            "BrainService starting (consumer=%s, patterns=%s)",
            self._consumer_name,
            self._stream_patterns,
        )

        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)

        await self._load_checkpoints()

        while not self._stop_event.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unhandled error in BrainService main loop; continuing")
                await asyncio.sleep(1)

        logger.info("BrainService stopped")
        self._running = False

    def stop(self) -> None:
        """Signal the loop to shut down cleanly."""
        logger.info("BrainService.stop() called")
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Internal loop tick
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        streams = await self._discover_streams()
        if not streams:
            await asyncio.sleep(1.0)
            return

        read_spec: dict[str, str] = {
            s: self._checkpoints.get(s, "0-0") for s in streams
        }

        try:
            assert self._redis is not None
            results = await self._redis.xread(read_spec, count=50, block=_BLOCK_MS)
        except Exception:
            logger.exception("XREAD error; sleeping 5s")
            await asyncio.sleep(5)
            return

        if not results:
            return

        for stream_raw, entries in results:
            # Redis may return bytes or str depending on decode_responses
            stream = stream_raw if isinstance(stream_raw, str) else stream_raw.decode()
            for entry_id_raw, fields_raw in entries:
                entry_id = (
                    entry_id_raw
                    if isinstance(entry_id_raw, str)
                    else entry_id_raw.decode()
                )
                # Normalise field keys/values
                fields: dict[str, str] = {
                    (k if isinstance(k, str) else k.decode()): (
                        v if isinstance(v, str) else v.decode()
                    )
                    for k, v in (
                        fields_raw.items()
                        if hasattr(fields_raw, "items")
                        else fields_raw
                    )
                }

                # Idempotency — check message_id in the envelope
                msg_id = fields.get("message_id", "") or entry_id
                if msg_id in self._seen_ids:
                    logger.debug("Duplicate message_id=%s, skipping", msg_id)
                    # Still advance checkpoint so we don't re-read indefinitely
                    self._checkpoints[stream] = entry_id
                    await self._persist_checkpoint(stream, entry_id)
                    continue

                handler_raised = False
                try:
                    await self._handle_event(stream, entry_id, fields)
                except Exception:
                    logger.exception(
                        "Handler failed on stream=%s entry=%s; skipping (fail-open)",
                        stream,
                        entry_id,
                    )
                    handler_raised = True

                # Only advance checkpoint if handler succeeded
                if not handler_raised:
                    self._seen_ids.add(msg_id)
                    self._checkpoints[stream] = entry_id
                    await self._persist_checkpoint(stream, entry_id)

    # ------------------------------------------------------------------
    # Stream discovery
    # ------------------------------------------------------------------

    async def _discover_streams(self) -> list[str]:
        """SCAN redis for all streams matching our patterns."""
        found: list[str] = []
        assert self._redis is not None
        for pattern in self._stream_patterns:
            cursor: int = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=pattern, count=100
                )
                for k in keys:
                    key = k if isinstance(k, str) else k.decode()
                    if key not in found:
                        found.append(key)
                if cursor == 0:
                    break
        return found

    # ------------------------------------------------------------------
    # Checkpoint persistence
    # ------------------------------------------------------------------

    async def _load_checkpoints(self) -> None:
        """Load all known checkpoints from redis."""
        assert self._redis is not None
        # SCAN for checkpoint keys belonging to this consumer
        cursor: int = 0
        prefix = f"{_CHECKPOINT_KEY_PREFIX}:*"
        while True:
            cursor, keys = await self._redis.scan(cursor, match=prefix, count=100)
            for k in keys:
                key = k if isinstance(k, str) else k.decode()
                val = await self._redis.get(key)
                if val:
                    # key format: sos:consumer:brain:checkpoint:<stream>
                    # stream name starts after the fixed prefix + ":"
                    stream_name = key[len(_CHECKPOINT_KEY_PREFIX) + 1:]
                    self._checkpoints[stream_name] = (
                        val if isinstance(val, str) else val.decode()
                    )
                    logger.debug("Loaded checkpoint %s → %s", stream_name, val)
            if cursor == 0:
                break
        logger.info("Loaded %d stream checkpoint(s)", len(self._checkpoints))

    async def _persist_checkpoint(self, stream: str, entry_id: str) -> None:
        """Write checkpoint for one stream to redis."""
        key = f"{_CHECKPOINT_KEY_PREFIX}:{stream}"
        assert self._redis is not None
        await self._redis.set(key, entry_id)

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def _handle_event(
        self, stream: str, entry_id: str, fields: dict[str, str]
    ) -> None:
        """Parse v1 envelope and dispatch to the appropriate handler stub."""
        msg_type = fields.get("type", "")
        now_iso = datetime.now(timezone.utc).isoformat()

        # Parse payload JSON — tolerate malformed payloads
        raw_payload = fields.get("payload", "{}")
        try:
            payload: dict = json.loads(raw_payload) if raw_payload else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Malformed payload on stream=%s entry=%s; skipping", stream, entry_id
            )
            # Update state counters even for malformed messages
            self.state.record_event("_malformed", now_iso)
            return

        msg: dict = {"type": msg_type, "stream": stream, "entry_id": entry_id, **payload}

        if msg_type not in _BRAIN_HANDLED_TYPES:
            logger.debug(
                "Unhandled type=%r on stream=%s; skipping gracefully", msg_type, stream
            )
            self.state.record_event(msg_type or "_unknown", now_iso)
            return

        # Update state counters
        self.state.record_event(msg_type, now_iso)

        # Dispatch
        if msg_type == "task.created":
            await self._on_task_created(msg)
        elif msg_type == "task.completed":
            await self._on_task_completed(msg)
        elif msg_type == "task.failed":
            await self._on_task_failed(msg)
        elif msg_type == "task.routed":
            await self._on_task_routed(msg)
        elif msg_type == "agent_joined":
            await self._on_agent_joined(msg)

    # ------------------------------------------------------------------
    # Handler stubs (Sprint 1 — log + count only)
    # Sprint 3 adds scoring + ProviderMatrix dispatch.
    # ------------------------------------------------------------------

    async def _on_task_created(self, msg: dict) -> None:
        """Stub: log + track in-flight. Sprint 3 adds scoring + dispatch."""
        task_id = msg.get("task_id") or msg.get("id", "unknown")
        logger.info(
            "[brain] task.created task_id=%s title=%r",
            task_id,
            msg.get("title", ""),
        )
        self.state.tasks_in_flight.add(task_id)

    async def _on_task_completed(self, msg: dict) -> None:
        """Stub: log + remove from in-flight."""
        task_id = msg.get("task_id") or msg.get("id", "unknown")
        logger.info("[brain] task.completed task_id=%s", task_id)
        self.state.tasks_in_flight.discard(task_id)

    async def _on_task_failed(self, msg: dict) -> None:
        """Stub: log + remove from in-flight."""
        task_id = msg.get("task_id") or msg.get("id", "unknown")
        logger.warning("[brain] task.failed task_id=%s reason=%r", task_id, msg.get("reason", ""))
        self.state.tasks_in_flight.discard(task_id)

    async def _on_task_routed(self, msg: dict) -> None:
        """Stub: log. Sprint 3 generates these; for now we observe them."""
        logger.info(
            "[brain] task.routed task_id=%s agent=%r",
            msg.get("task_id", "unknown"),
            msg.get("agent", ""),
        )

    async def _on_agent_joined(self, msg: dict) -> None:
        """Stub: log new agent join event."""
        logger.info(
            "[brain] agent_joined name=%r model=%r",
            msg.get("name", "unknown"),
            msg.get("model", ""),
        )
