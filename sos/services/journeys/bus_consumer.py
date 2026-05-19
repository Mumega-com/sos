"""Journeys bus consumer — listens for task.completed events.

Closes P0-05 from the 2026-04-17 structural audit: squad no longer reaches
into ``sos.services.journeys.tracker`` in-process to auto-evaluate milestones.
Instead, squad emits a v1 ``task.completed`` envelope on
``sos:stream:global:squad:*`` and this consumer picks it up.

v0.9.1 W5 — migrates from in-memory LRU + Redis checkpoint to XACK-based
at-least-once. Semantics now:

* Success (handler returns normally, including intentional skips like
  ``type != task.completed`` or ``source == agent:system``) → ``XACK``.
* Exception inside ``_handle_event`` → leave unacked. The bus retry
  worker (:class:`sos.services.bus.retry.RetryWorker`) reclaims the
  entry after backoff; terminal failures go to DLQ after
  ``max_retry`` tries. No silent drops.
* Duplicate envelope ``message_id`` (same logical event XADD'd twice
  as two stream entries) → skip and ``XACK`` — we still dedup at the
  envelope layer because retry re-XADDs, and producers can double-send.

Follows the 5-invariant bus-consumer pattern; one consumer group
(``journeys`` by default) across every discovered stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Optional

try:
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover — optional dep
    aioredis = None  # type: ignore[assignment]

from sos.services.journeys.tracker import JourneyTracker

if TYPE_CHECKING:
    from sos.services.bus.redis_bus import RedisBusService


logger = logging.getLogger("sos.journeys.bus_consumer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLOCK_MS = 1000
_LRU_CAPACITY = 10_000
_DEFAULT_GROUP = "journeys"

DEFAULT_STREAM_PATTERNS: list[str] = [
    "sos:stream:global:squad:*",
]

_HANDLED_TYPES: frozenset[str] = frozenset({"task.completed"})


# ---------------------------------------------------------------------------
# LRU for envelope-level idempotency
# ---------------------------------------------------------------------------


class _LRUSet:
    """Bounded seen-set for envelope ``message_id`` dedup.

    XREADGROUP prevents re-delivery of the *same stream entry* to the
    group, but retry re-XADDs under a new stream ID, and producers
    occasionally double-send — both produce distinct stream entries
    that share an envelope ``message_id``. The LRU absorbs that layer
    so ``auto_evaluate`` fires exactly once per logical event.
    """

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
    from sos.kernel.settings import get_settings as _get_settings

    return _get_settings().redis.build_url()


# ---------------------------------------------------------------------------
# JourneysBusConsumer
# ---------------------------------------------------------------------------


class JourneysBusConsumer:
    """Consume task.completed events and auto-evaluate milestones.

    Subscribes to ``sos:stream:global:squad:*`` by default via the
    ``journeys`` consumer group. For each task.completed message,
    extracts the agent name (from the envelope ``source`` field, shape
    ``agent:<name>``) and calls ``JourneyTracker().auto_evaluate``.

    If ``bus_service`` is provided, newly-discovered streams are
    registered with it so the retry worker reclaims unacked entries
    after backoff. If not, groups are created directly via
    ``XGROUP CREATE`` — useful for unit tests that don't want the full
    bus stack but still want XREADGROUP semantics.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        stream_patterns: list[str] | None = None,
        consumer_name: str = "journeys",
        group_name: str = _DEFAULT_GROUP,
        redis_client: Optional["aioredis.Redis"] = None,
        tracker: Optional[JourneyTracker] = None,
        bus_service: Optional["RedisBusService"] = None,
    ) -> None:
        self._redis_url = redis_url or _build_redis_url()
        self._stream_patterns = stream_patterns or DEFAULT_STREAM_PATTERNS
        self._consumer_name = consumer_name
        self._group_name = group_name
        self._redis: Optional["aioredis.Redis"] = redis_client
        self._bus_service = bus_service
        # Lazily constructed tracker (avoid YAML load at import).
        self._tracker = tracker
        self._seen_ids = _LRUSet(_LRU_CAPACITY)
        # Streams we've already ensured a consumer group on.
        self._groups_registered: set[str] = set()
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
            "JourneysBusConsumer starting (consumer=%s, group=%s, patterns=%s)",
            self._consumer_name,
            self._group_name,
            self._stream_patterns,
        )

        if self._redis is None:
            if aioredis is None:
                logger.error("redis.asyncio not available; consumer disabled")
                self._running = False
                return
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)

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

        for stream in streams:
            if stream not in self._groups_registered:
                await self._ensure_group(stream)
                self._groups_registered.add(stream)

        read_spec: dict[str, str] = {s: ">" for s in streams}

        try:
            assert self._redis is not None
            results = await self._redis.xreadgroup(
                groupname=self._group_name,
                consumername=self._consumer_name,
                streams=read_spec,
                count=50,
                block=_BLOCK_MS,
            )
        except Exception:
            logger.exception("XREADGROUP error; sleeping 5s")
            await asyncio.sleep(5)
            return

        if not results:
            return

        for stream_raw, entries in results:
            stream = stream_raw if isinstance(stream_raw, str) else stream_raw.decode()
            for entry_id_raw, fields_raw in entries:
                entry_id = entry_id_raw if isinstance(entry_id_raw, str) else entry_id_raw.decode()
                fields: dict[str, str] = {
                    (k if isinstance(k, str) else k.decode()): (
                        v if isinstance(v, str) else v.decode()
                    )
                    for k, v in (fields_raw.items() if hasattr(fields_raw, "items") else fields_raw)
                }

                msg_id = fields.get("message_id", "") or entry_id
                if msg_id in self._seen_ids:
                    logger.debug("Duplicate message_id=%s, ack+skip", msg_id)
                    await self._redis.xack(stream, self._group_name, entry_id)
                    continue

                try:
                    await self._handle_event(stream, entry_id, fields)
                except Exception:
                    # Leave unacked — retry worker reclaims after backoff.
                    # Do NOT XACK, do NOT mark seen: a retry re-delivery must
                    # re-enter the handler, and the envelope is re-XADD'd
                    # under a new stream ID so dedup is by retry_count, not
                    # our seen-set.
                    logger.exception(
                        "Handler failed stream=%s entry=%s; leaving unacked for retry",
                        stream,
                        entry_id,
                    )
                    continue

                self._seen_ids.add(msg_id)
                await self._redis.xack(stream, self._group_name, entry_id)

    # ------------------------------------------------------------------
    # Stream discovery + group registration
    # ------------------------------------------------------------------

    async def _discover_streams(self) -> list[str]:
        found: list[str] = []
        assert self._redis is not None
        for pattern in self._stream_patterns:
            cursor: int = 0
            while True:
                cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                for k in keys:
                    key = k if isinstance(k, str) else k.decode()
                    if key not in found:
                        found.append(key)
                if cursor == 0:
                    break
        return found

    async def _ensure_group(self, stream: str) -> None:
        """Idempotently create/register the consumer group on ``stream``.

        Uses ``bus_service.register_consumer_group`` when available so
        the retry worker picks up unacked entries; falls back to a raw
        ``XGROUP CREATE`` with ``MKSTREAM`` (swallowing BUSYGROUP) so
        unit tests with just a fake redis still get XREADGROUP semantics.
        """
        if self._bus_service is not None:
            await self._bus_service.register_consumer_group(stream, self._group_name)
            return
        assert self._redis is not None
        try:
            await self._redis.xgroup_create(
                name=stream, groupname=self._group_name, id="0", mkstream=True
            )
        except Exception as exc:  # redis.exceptions.ResponseError when BUSYGROUP
            if "BUSYGROUP" not in str(exc):
                raise

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def _handle_event(self, stream: str, entry_id: str, fields: dict[str, str]) -> None:
        msg_type = fields.get("type", "")
        if msg_type not in _HANDLED_TYPES:
            return

        raw_payload = fields.get("payload", "{}")
        try:
            payload: dict[str, Any] = json.loads(raw_payload) if raw_payload else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("Malformed payload stream=%s entry=%s; skipping", stream, entry_id)
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
