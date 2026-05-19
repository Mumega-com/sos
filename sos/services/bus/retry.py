"""Bus retry worker — exponential backoff for unacked stream messages.

Runs as a background task alongside :class:`RedisBusService`. Every tick
(``scan_interval`` seconds, 30s by default), scans ``XPENDING`` for each
registered ``(stream, group)`` pair and routes entries whose idle time
has exceeded their per-delivery-count backoff:

* ``deliveries == 1`` (claimed once, never acked) → wait 30s, then retry
* ``deliveries == 2`` → wait 120s (2m), then retry
* ``deliveries == 3`` → wait 600s (10m), then retry
* ``deliveries >= 4`` → DLQ + XACK (retry budget exhausted)

"Retry" here means: ``XACK`` the pending entry and ``XADD`` the payload
back to the same stream with ``__retry_count`` bumped. The consumer's
normal ``XREADGROUP '>' `` loop picks it up as a fresh message — no
consumer-side changes needed for W3. (W5 will migrate the journeys
consumer to XACK-based at-least-once; this worker is what makes that
safe.)

DLQ entries land on ``sos:stream:dlq:{original_stream}`` using the
shared schema in :mod:`sos.services.bus.dlq` — same field names the
dashboard read route and any ops scripts rely on, so writer and
reader can never drift.

Ordering note: we ``XADD`` before ``XACK`` on retry. A duplicate is
recoverable (idempotent consumer); a lost message isn't.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Mapping, Optional

from sos.services.bus.dlq import build_dlq_fields, dlq_stream_for

if TYPE_CHECKING:
    from sos.services.bus.redis_bus import RedisBusService

logger = logging.getLogger("sos.bus.retry")


# Delivery-count → backoff seconds. Anything above the max key goes to DLQ.
DEFAULT_BACKOFFS_SECONDS: Mapping[int, int] = {1: 30, 2: 120, 3: 600}
DEFAULT_SCAN_INTERVAL_SECONDS: int = 30


class RetryWorker:
    """Scan ``XPENDING`` and route retries + DLQ.

    Not a singleton — one instance per bus service is expected. The
    worker is started lazily by the bus lifecycle and stopped on
    ``disconnect``; ``start()`` and ``stop()`` are both idempotent so
    callers don't need to track state.

    ``backoffs`` and ``scan_interval`` are injectable so integration
    tests can drive the scan loop at sub-second cadence without waiting
    for production-sized backoffs.
    """

    def __init__(
        self,
        bus: "RedisBusService",
        scan_interval: int = DEFAULT_SCAN_INTERVAL_SECONDS,
        backoffs: Optional[Mapping[int, int]] = None,
    ):
        self.bus = bus
        self.scan_interval = scan_interval
        self.backoffs: Mapping[int, int] = dict(backoffs or DEFAULT_BACKOFFS_SECONDS)
        # Any delivery count strictly greater than max_retry → DLQ.
        self.max_retry: int = max(self.backoffs) if self.backoffs else 0
        self._registrations: set[tuple[str, str]] = set()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # --- registration ----------------------------------------------------

    def register(self, stream: str, group: str) -> None:
        self._registrations.add((stream, group))

    def unregister(self, stream: str, group: str) -> None:
        self._registrations.discard((stream, group))

    @property
    def registrations(self) -> set[tuple[str, str]]:
        return set(self._registrations)

    # --- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        """Start the background scan loop. Idempotent."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the scan loop and wait for it to exit cleanly."""
        self._running = False
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # --- scan loop -------------------------------------------------------

    async def _loop(self) -> None:
        try:
            while self._running:
                try:
                    await self.scan_once()
                except Exception as exc:
                    logger.error("retry scan error: %s", exc)
                await asyncio.sleep(self.scan_interval)
        except asyncio.CancelledError:
            raise

    async def scan_once(self) -> None:
        """One pass over every registered (stream, group).

        Public so integration tests can drive it deterministically
        instead of waiting for ``scan_interval`` to elapse.
        """
        for stream, group in list(self._registrations):
            await self._scan_pair(stream, group)

    # --- per-pair logic --------------------------------------------------

    async def _scan_pair(self, stream: str, group: str) -> None:
        if not self.bus.is_connected:
            return
        pending = await self.bus.client.xpending_range(stream, group, min="-", max="+", count=100)
        for entry in pending:
            await self._route(stream, group, entry)

    async def _route(self, stream: str, group: str, entry: dict) -> None:
        message_id = entry["message_id"]
        idle_ms = entry["time_since_delivered"]
        deliveries = entry["times_delivered"]

        if deliveries > self.max_retry:
            await self._to_dlq(stream, group, message_id, deliveries)
            return

        backoff = self.backoffs.get(deliveries)
        if backoff is None:
            # Unknown bucket (e.g. deliveries == 0). Skip.
            return
        if idle_ms < backoff * 1000:
            return

        await self._retry(stream, group, message_id, deliveries)

    async def _retry(self, stream: str, group: str, message_id: str, deliveries: int) -> None:
        """XADD a copy with ``__retry_count`` bumped, then XACK the original.

        If the underlying entry has already been trimmed out of the
        stream (``xrange`` returns nothing), XACK anyway — there's
        nothing to re-publish.
        """
        raw = await self.bus.client.xrange(stream, min=message_id, max=message_id, count=1)
        if not raw:
            await self.bus.client.xack(stream, group, message_id)
            return

        _, fields = raw[0]
        fields = dict(fields)
        prior = int(fields.get("__retry_count", "0") or 0)
        fields["__retry_count"] = str(prior + 1)

        await self.bus.client.xadd(stream, fields)
        await self.bus.client.xack(stream, group, message_id)
        logger.info(
            "retry stream=%s group=%s id=%s deliveries=%d",
            stream,
            group,
            message_id,
            deliveries,
        )

    async def _to_dlq(self, stream: str, group: str, message_id: str, deliveries: int) -> None:
        """Write DLQ entry + XACK. Schema owned by :mod:`sos.services.bus.dlq`."""
        raw = await self.bus.client.xrange(stream, min=message_id, max=message_id, count=1)
        payload = dict(raw[0][1]) if raw else {}
        dlq_entry = build_dlq_fields(
            original_stream=stream,
            original_id=message_id,
            group=group,
            retry_count=deliveries,
            payload=payload,
        )
        await self.bus.client.xadd(dlq_stream_for(stream), dlq_entry, maxlen=10000)
        await self.bus.client.xack(stream, group, message_id)
        logger.warning(
            "DLQ stream=%s group=%s id=%s deliveries=%d",
            stream,
            group,
            message_id,
            deliveries,
        )
