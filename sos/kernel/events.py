"""SOS Event System — publish/subscribe for services.

Services emit events. Other services react.
The nervous system that makes the organism responsive.

Examples:
- "task.completed" -> feedback loop scores the result
- "analytics.ingested" -> decision agent wakes up
- "tenant.created" -> welcome email sent
- "agent.joined" -> sentinel checks identity
- "payment.received" -> provision workstation
- "content.published" -> analytics tracks new page
- "health.degraded" -> calcifer escalates

Uses Redis pub/sub. Lightweight. No message broker needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Awaitable
from uuid import uuid4

import redis.asyncio as aioredis

logger = logging.getLogger("sos.events")

# ---------------------------------------------------------------------------
# Predefined event types
# ---------------------------------------------------------------------------

# Tenant lifecycle
TENANT_CREATED = "tenant.created"
TENANT_DELETED = "tenant.deleted"
PAYMENT_RECEIVED = "payment.received"
PAYMENT_FAILED = "payment.failed"

# Agent lifecycle
AGENT_JOINED = "agent.joined"
AGENT_LEFT = "agent.left"
AGENT_CHALLENGED = "agent.challenged"

# Task lifecycle
TASK_CREATED = "task.created"
TASK_CLAIMED = "task.claimed"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"

# Content lifecycle
CONTENT_PUBLISHED = "content.published"
CONTENT_UPDATED = "content.updated"

# Analytics lifecycle
ANALYTICS_INGESTED = "analytics.ingested"
DECISION_MADE = "decision.made"
ACTION_EXECUTED = "action.executed"
FEEDBACK_SCORED = "feedback.scored"

# Health
HEALTH_DEGRADED = "health.degraded"
HEALTH_RECOVERED = "health.recovered"
SERVICE_REGISTERED = "service.registered"
SERVICE_DOWN = "service.down"

# Channel prefix for Redis pub/sub
_CHANNEL_PREFIX = "sos:events:"
# Stream key for audit log
_STREAM_KEY = "sos:events:log"

# Type alias for event handlers
EventHandler = Callable[[dict], Awaitable[None]]


def _channel_name(event_type: str) -> str:
    """Redis channel name for an event type."""
    return f"{_CHANNEL_PREFIX}{event_type}"


class EventBus:
    """Publish/subscribe event bus backed by Redis.

    Provides emit (publish), subscribe, listen (blocking), and replay.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        from sos.kernel.settings import get_settings as _get_settings
        _s = _get_settings().redis
        url = redis_url or _s.legacy_sos_url or _s.resolved_url
        self._redis: aioredis.Redis = aioredis.from_url(
            url, decode_responses=True
        )
        self._subscriptions: list[aioredis.client.PubSub] = []
        self._listener_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    async def emit(
        self,
        event_type: str,
        data: dict | None = None,
        source: str | None = None,
    ) -> str:
        """Publish an event to Redis pub/sub and persist to the event log.

        Returns the event id.
        """
        event_id = str(uuid4())
        event = {
            "type": event_type,
            "data": json.dumps(data or {}),
            "source": source or "unknown",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "id": event_id,
        }

        # Publish to channel (fan-out to subscribers)
        channel = _channel_name(event_type)
        await self._redis.publish(channel, json.dumps(event))

        # Persist to stream for replay / audit
        await self._redis.xadd(_STREAM_KEY, event, maxlen=10_000)

        logger.info("Event emitted: %s from %s (id=%s)", event_type, event["source"], event_id)
        return event_id

    # ------------------------------------------------------------------
    # Subscribe (non-blocking)
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Callable[[], Awaitable[None]]:
        """Subscribe to one or more event types.

        Returns an async unsubscribe function.
        """
        pubsub = self._redis.pubsub()
        channels = {_channel_name(et): et for et in event_types}
        await pubsub.subscribe(*channels.keys())
        self._subscriptions.append(pubsub)

        async def _reader() -> None:
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        event = json.loads(message["data"])
                        # Decode nested data back to dict
                        if isinstance(event.get("data"), str):
                            event["data"] = json.loads(event["data"])
                        await handler(event)
                    except Exception:
                        logger.exception(
                            "Handler error for %s", message.get("channel")
                        )
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_reader())
        self._listener_tasks.append(task)

        async def unsubscribe() -> None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await pubsub.unsubscribe()
            await pubsub.aclose()
            if pubsub in self._subscriptions:
                self._subscriptions.remove(pubsub)
            if task in self._listener_tasks:
                self._listener_tasks.remove(task)

        return unsubscribe

    # ------------------------------------------------------------------
    # Listen (blocking — for long-running services)
    # ------------------------------------------------------------------

    async def listen(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> None:
        """Blocking listener. Runs until cancelled.

        Use this in services that should react to events forever.
        """
        pubsub = self._redis.pubsub()
        channels = {_channel_name(et): et for et in event_types}
        await pubsub.subscribe(*channels.keys())
        self._subscriptions.append(pubsub)

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    if isinstance(event.get("data"), str):
                        event["data"] = json.loads(event["data"])
                    await handler(event)
                except Exception:
                    logger.exception(
                        "Handler error for %s", message.get("channel")
                    )
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            if pubsub in self._subscriptions:
                self._subscriptions.remove(pubsub)

    # ------------------------------------------------------------------
    # Replay (read from stream for debugging / catchup)
    # ------------------------------------------------------------------

    async def replay(
        self,
        event_type: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Read events from the persistent log stream.

        Args:
            event_type: Filter by event type (None = all).
            since: ISO timestamp or Redis stream ID. Defaults to beginning.
            limit: Max events to return.

        Returns:
            List of event dicts, oldest first.
        """
        # Convert ISO timestamp to a Redis stream ms-timestamp
        start = "0-0"
        if since:
            try:
                dt = datetime.fromisoformat(since)
                start = f"{int(dt.timestamp() * 1000)}-0"
            except ValueError:
                # Assume it is already a stream ID
                start = since

        raw = await self._redis.xrange(_STREAM_KEY, min=start, count=limit)

        events: list[dict] = []
        for _stream_id, fields in raw:
            if event_type and fields.get("type") != event_type:
                continue
            event = dict(fields)
            # Decode data field back to dict
            if isinstance(event.get("data"), str):
                try:
                    event["data"] = json.loads(event["data"])
                except (json.JSONDecodeError, TypeError):
                    pass
            events.append(event)

        return events

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Cancel all listeners and close Redis connections."""
        for task in self._listener_tasks:
            task.cancel()
        if self._listener_tasks:
            await asyncio.gather(*self._listener_tasks, return_exceptions=True)
        for pubsub in self._subscriptions:
            try:
                await pubsub.unsubscribe()
                await pubsub.aclose()
            except Exception:
                pass
        self._subscriptions.clear()
        self._listener_tasks.clear()
        await self._redis.aclose()
