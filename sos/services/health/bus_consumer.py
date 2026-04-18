"""Health bus consumer — listens for task.completed events.

Closes P0-04 from the 2026-04-17 structural audit: squad no longer reaches
into ``sos.services.health.calcifer`` in-process to update conductance.
Instead, squad emits a v1 ``task.completed`` envelope on
``sos:stream:global:squad:*`` and this consumer picks it up.

Follows the 5-invariant bus-consumer pattern established by
``sos.services.brain.service.BrainService``:

1. Idempotency on ``message_id`` — a small LRU guards against replays.
2. Per-stream checkpoints in
   ``sos:consumer:health:checkpoint:<stream>``.
3. Fail-open on handler exceptions; the loop keeps ticking.
4. SCAN-based stream discovery; no hardcoded stream names.
5. Replay tolerance — combined with idempotency, reprocessing the same
   event never double-updates conductance.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict
from typing import Any, Optional

try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover — optional dep
    aioredis = None  # type: ignore[assignment]

from sos.services.health.calcifer import conductance_update


logger = logging.getLogger("sos.health.bus_consumer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHECKPOINT_KEY_PREFIX = "sos:consumer:health:checkpoint"
_BLOCK_MS = 1000
_LRU_CAPACITY = 10_000

DEFAULT_STREAM_PATTERNS: list[str] = [
    "sos:stream:global:squad:*",
]

_HANDLED_TYPES: frozenset[str] = frozenset({"task.completed"})


# ---------------------------------------------------------------------------
# LRU for idempotency — same shape as brain.service._LRUSet
# ---------------------------------------------------------------------------


class _LRUSet:
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
# Redis URL helper
# ---------------------------------------------------------------------------


def _build_redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "")
    if password:
        return f"redis://:{password}@{host}:{port}"
    return f"redis://{host}:{port}"


# ---------------------------------------------------------------------------
# HealthBusConsumer
# ---------------------------------------------------------------------------


class HealthBusConsumer:
    """Consume task.completed events and update the conductance network.

    Subscribes to ``sos:stream:global:squad:*`` by default. Each tick:

    - Discovers matching streams via SCAN.
    - Reads new entries past each stream's checkpoint.
    - Skips duplicates by ``message_id``.
    - Calls ``conductance_update(agent_addr, label, reward)`` for each label
      when ``result.reward_mind > 0``.
    - Advances + persists the checkpoint on success.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        stream_patterns: list[str] | None = None,
        consumer_name: str = "health",
        redis_client: Optional["aioredis.Redis"] = None,
    ) -> None:
        self._redis_url = redis_url or _build_redis_url()
        self._stream_patterns = stream_patterns or DEFAULT_STREAM_PATTERNS
        self._consumer_name = consumer_name
        self._redis: Optional["aioredis.Redis"] = redis_client
        self._checkpoints: dict[str, str] = {}
        self._seen_ids = _LRUSet(_LRU_CAPACITY)
        self._stop_event: asyncio.Event = asyncio.Event()
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        self._stop_event.clear()
        logger.info(
            "HealthBusConsumer starting (consumer=%s, patterns=%s)",
            self._consumer_name,
            self._stream_patterns,
        )

        if self._redis is None:
            if aioredis is None:
                logger.error("redis.asyncio not available; consumer disabled")
                self._running = False
                return
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)

        await self._load_checkpoints()

        while not self._stop_event.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unhandled error in HealthBusConsumer; continuing")
                await asyncio.sleep(1)

        logger.info("HealthBusConsumer stopped")
        self._running = False

    def stop(self) -> None:
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
            stream = stream_raw if isinstance(stream_raw, str) else stream_raw.decode()
            for entry_id_raw, fields_raw in entries:
                entry_id = (
                    entry_id_raw
                    if isinstance(entry_id_raw, str)
                    else entry_id_raw.decode()
                )
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

                msg_id = fields.get("message_id", "") or entry_id
                if msg_id in self._seen_ids:
                    logger.debug("Duplicate message_id=%s, skipping", msg_id)
                    self._checkpoints[stream] = entry_id
                    await self._persist_checkpoint(stream, entry_id)
                    continue

                handler_raised = False
                try:
                    await self._handle_event(stream, entry_id, fields)
                except Exception:
                    logger.exception(
                        "Handler failed stream=%s entry=%s; skipping (fail-open)",
                        stream,
                        entry_id,
                    )
                    handler_raised = True

                if not handler_raised:
                    self._seen_ids.add(msg_id)
                    self._checkpoints[stream] = entry_id
                    await self._persist_checkpoint(stream, entry_id)

    # ------------------------------------------------------------------
    # Stream discovery
    # ------------------------------------------------------------------

    async def _discover_streams(self) -> list[str]:
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
        assert self._redis is not None
        cursor: int = 0
        prefix_match = f"{_CHECKPOINT_KEY_PREFIX}:*"
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=prefix_match, count=100
            )
            for k in keys:
                key = k if isinstance(k, str) else k.decode()
                val = await self._redis.get(key)
                if val:
                    stream_name = key[len(_CHECKPOINT_KEY_PREFIX) + 1:]
                    self._checkpoints[stream_name] = (
                        val if isinstance(val, str) else val.decode()
                    )
            if cursor == 0:
                break
        logger.info("Loaded %d health-consumer checkpoint(s)", len(self._checkpoints))

    async def _persist_checkpoint(self, stream: str, entry_id: str) -> None:
        key = f"{_CHECKPOINT_KEY_PREFIX}:{stream}"
        assert self._redis is not None
        await self._redis.set(key, entry_id)

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def _handle_event(
        self, stream: str, entry_id: str, fields: dict[str, str]
    ) -> None:
        msg_type = fields.get("type", "")
        if msg_type not in _HANDLED_TYPES:
            return

        raw_payload = fields.get("payload", "{}")
        try:
            payload: dict[str, Any] = json.loads(raw_payload) if raw_payload else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Malformed payload stream=%s entry=%s; skipping", stream, entry_id
            )
            return

        if msg_type == "task.completed":
            await self._on_task_completed(payload)

    async def _on_task_completed(self, payload: dict[str, Any]) -> None:
        """Apply conductance_update for each (agent, label) pair.

        Squad stuffs the extras we need into ``payload.result``:
          - ``agent_addr``: the agent that did the work
          - ``labels``: list of skill labels for the task
          - ``reward_mind``: $MIND reward (conductance flow magnitude)
        """
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        result = result or {}

        reward_raw = result.get("reward_mind", 0)
        try:
            reward = float(reward_raw or 0)
        except (TypeError, ValueError):
            reward = 0.0
        if reward <= 0:
            return

        agent_addr = result.get("agent_addr") or result.get("assignee")
        if not agent_addr:
            return

        labels = result.get("labels") or []
        if not isinstance(labels, list):
            return

        for label in labels:
            if not isinstance(label, str) or not label:
                continue
            # conductance_update is sync file I/O; run off the loop.
            await asyncio.to_thread(conductance_update, agent_addr, label, reward)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [health.bus] %(levelname)s %(message)s",
    )
    consumer = HealthBusConsumer()
    await consumer.run()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
