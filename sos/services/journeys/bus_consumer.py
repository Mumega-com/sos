"""Journeys bus consumer — listens for task.completed events.

Closes P0-05 from the 2026-04-17 structural audit: squad no longer reaches
into ``sos.services.journeys.tracker`` in-process to auto-evaluate milestones.
Instead, squad emits a v1 ``task.completed`` envelope on
``sos:stream:global:squad:*`` and this consumer picks it up.

Follows the 5-invariant bus-consumer pattern established by
``sos.services.brain.service.BrainService``.
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

from sos.services.journeys.tracker import JourneyTracker


logger = logging.getLogger("sos.journeys.bus_consumer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHECKPOINT_KEY_PREFIX = "sos:consumer:journeys:checkpoint"
_BLOCK_MS = 1000
_LRU_CAPACITY = 10_000

DEFAULT_STREAM_PATTERNS: list[str] = [
    "sos:stream:global:squad:*",
]

_HANDLED_TYPES: frozenset[str] = frozenset({"task.completed"})


# ---------------------------------------------------------------------------
# LRU for idempotency
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
# JourneysBusConsumer
# ---------------------------------------------------------------------------


class JourneysBusConsumer:
    """Consume task.completed events and auto-evaluate milestones.

    Subscribes to ``sos:stream:global:squad:*`` by default. For each
    task.completed message, extracts the agent name (from the envelope
    ``source`` field, shape ``agent:<name>``) and calls
    ``JourneyTracker().auto_evaluate(agent_name)``.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        stream_patterns: list[str] | None = None,
        consumer_name: str = "journeys",
        redis_client: Optional["aioredis.Redis"] = None,
        tracker: Optional[JourneyTracker] = None,
    ) -> None:
        self._redis_url = redis_url or _build_redis_url()
        self._stream_patterns = stream_patterns or DEFAULT_STREAM_PATTERNS
        self._consumer_name = consumer_name
        self._redis: Optional["aioredis.Redis"] = redis_client
        self._tracker = tracker  # Lazily constructed if None (avoid YAML load at import)
        self._checkpoints: dict[str, str] = {}
        self._seen_ids = _LRUSet(_LRU_CAPACITY)
        self._stop_event: asyncio.Event = asyncio.Event()
        self._running = False
        # Observable state — number of auto_evaluate calls that actually fired.
        self.evaluations_performed: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        self._stop_event.clear()
        logger.info(
            "JourneysBusConsumer starting (consumer=%s, patterns=%s)",
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
                logger.exception("Unhandled error in JourneysBusConsumer; continuing")
                await asyncio.sleep(1)

        logger.info("JourneysBusConsumer stopped")
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
        logger.info("Loaded %d journeys-consumer checkpoint(s)", len(self._checkpoints))

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

        source = fields.get("source", "")
        agent_name = self._extract_agent_name(source, payload)
        if not agent_name or agent_name == "system":
            return

        if msg_type == "task.completed":
            await self._on_task_completed(agent_name)

    @staticmethod
    def _extract_agent_name(source: str, payload: dict[str, Any]) -> Optional[str]:
        """Pull agent name from ``source`` (shape ``agent:<name>``).

        Falls back to ``payload.result.agent_addr`` so we stay robust if squad's
        source normalization rewrote the name (see
        ``squad/tasks.py::_normalize_agent_source``).
        """
        if isinstance(source, str) and source.startswith("agent:"):
            name = source.split(":", 1)[1].strip()
            if name and name != "squad":
                return name
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        result = result or {}
        addr = result.get("agent_addr")
        if isinstance(addr, str) and addr:
            return addr
        return None

    def _get_tracker(self) -> JourneyTracker:
        if self._tracker is None:
            self._tracker = JourneyTracker()
        return self._tracker

    async def _on_task_completed(self, agent_name: str) -> None:
        def _evaluate() -> list[dict[str, Any]]:
            tracker = self._get_tracker()
            try:
                return tracker.auto_evaluate(agent_name)
            except Exception as exc:
                logger.debug("auto_evaluate skipped for %s: %s", agent_name, exc)
                return []

        completions = await asyncio.to_thread(_evaluate)
        self.evaluations_performed += 1
        for c in completions:
            logger.info(
                "Journey milestone: %s completed %s/%s (+%s MIND, badge: %s)",
                agent_name,
                c.get("path"),
                c.get("milestone"),
                c.get("reward_mind"),
                c.get("badge"),
            )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [journeys.bus] %(levelname)s %(message)s",
    )
    consumer = JourneysBusConsumer()
    await consumer.run()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
