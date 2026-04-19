"""
Redis Bus Implementation ⚡

Implements the BusContract using Redis Pub/Sub for channels and Lists/Streams for queues.
"""

import json
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Any, Optional
from ...contracts.bus import BusContract
from ...contracts.ports.bus import AckStatus, BusAck
from ...kernel.schema import Message

# Try to import redis, handle if missing
try:
    import redis.asyncio as redis
except ImportError:
    redis = None

logger = logging.getLogger("sos.bus")


class RedisBusService(BusContract):
    def __init__(self, redis_url: Optional[str] = None):
        if not redis:
            raise ImportError("redis-py is required. Install with: pip install redis")

        self.redis_url = redis_url or os.getenv("MUMEGA_REDIS_URL", "redis://localhost:6379/0")
        self.client: Optional[redis.Redis] = None
        self.pubsub = None
        self.is_connected = False

    async def connect(self) -> bool:
        try:
            self.client = redis.from_url(self.redis_url, decode_responses=True)
            await self.client.ping()
            self.is_connected = True
            logger.info(f"Connected to Redis Bus at {self.redis_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Redis Bus: {e}")
            return False

    async def disconnect(self):
        if self.client:
            await self.client.close()
            self.is_connected = False

    async def publish(self, channel: str, message: Message) -> bool:
        if not self.is_connected:
            await self.connect()

        try:
            # Serialize
            payload = json.dumps(message.to_dict())
            await self.client.publish(channel, payload)
            return True
        except Exception as e:
            logger.error(f"Publish error to {channel}: {e}")
            return False

    async def send(self, target_agent: str, message: Message) -> bool:
        if not self.is_connected:
            await self.connect()

        queue_key = f"agent:{target_agent}:inbox"
        try:
            payload = json.dumps(message.to_dict())
            await self.client.rpush(queue_key, payload)
            return True
        except Exception as e:
            logger.error(f"Send error to {target_agent}: {e}")
            return False

    async def subscribe(self, channel: str, callback: Callable[[Message], Any]):
        if not self.is_connected:
            await self.connect()

        if not self.pubsub:
            self.pubsub = self.client.pubsub()

        await self.pubsub.subscribe(channel)
        logger.info(f"Subscribed to {channel}")

        # Start listening loop in background task
        asyncio.create_task(self._listener_loop(callback))

    async def listen(self, agent_id: str, callback: Callable[[Message], Any]):
        """
        Listen to direct inbox (Blocking Pop loop).
        """
        if not self.is_connected:
            await self.connect()

        queue_key = f"agent:{agent_id}:inbox"
        logger.info(f"Listening to inbox: {queue_key}")

        asyncio.create_task(self._queue_loop(queue_key, callback))

    async def _listener_loop(self, callback):
        """Internal loop for Pub/Sub"""
        try:
            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        # Convert dict back to Message object if possible, or pass dict
                        # For now, we assume callback handles dict or we reconstitute
                        # msg_obj = Message(**data)
                        if asyncio.iscoroutinefunction(callback):
                            await callback(data)
                        else:
                            callback(data)
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
        except Exception as e:
            logger.error(f"Listener loop died: {e}")

    async def ensure_group(self, stream: str, group: str) -> None:
        """Idempotently create a consumer group on ``stream``.

        XGROUP CREATE raises BUSYGROUP if the group already exists; we
        swallow that one error and re-raise everything else. ``MKSTREAM``
        lets us create the group before any producer has XADD'd — useful
        for tests that ack before publishing.
        """
        if not self.is_connected:
            await self.connect()
        try:
            await self.client.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except Exception as exc:  # redis.exceptions.ResponseError when BUSYGROUP
            if "BUSYGROUP" not in str(exc):
                raise

    async def ack(
        self,
        stream: str,
        group: str,
        message_id: str,
        status: AckStatus = "ok",
    ) -> BusAck:
        """Acknowledge a consumed message by XACK on the consumer group.

        All three dispositions XACK (release from the PEL) — leaving a
        message pending forever blocks retry and starves the group. The
        ``status`` we return to the caller records *intent*:

        * ``ok``   — normal completion.
        * ``nack`` — soft failure; the delivery-layer retry worker (W3)
          will re-enqueue from the status signal.
        * ``dlq``  — terminal; the DLQ writer (W4) routes the corpse.

        Real retry/DLQ routing is not in this wave — we wire the verb
        and prove XACK reaches Redis; W3 and W4 plug in behind this
        interface without changing the contract.
        """
        if status not in ("ok", "nack", "dlq"):
            raise ValueError(f"invalid ack status: {status!r}")

        if not self.is_connected:
            await self.connect()

        try:
            await self.client.xack(stream, group, message_id)
        except Exception as exc:
            logger.error(
                "ack error on stream=%s group=%s id=%s: %s",
                stream,
                group,
                message_id,
                exc,
            )
            raise

        return BusAck(
            message_id=message_id,
            acked_at=datetime.now(timezone.utc).isoformat(),
            status=status,
        )

    async def _queue_loop(self, queue_key, callback):
        """Internal loop for Queue (BLPOP)"""
        while self.is_connected:
            try:
                # BLPOP returns (key, element) tuple
                # timeout=0 means block indefinitely
                result = await self.client.blpop(queue_key, timeout=1)
                if result:
                    _, data_str = result
                    data = json.loads(data_str)
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                else:
                    await asyncio.sleep(0.1)  # Yield
            except Exception as e:
                logger.error(f"Queue loop error: {e}")
                await asyncio.sleep(1)
