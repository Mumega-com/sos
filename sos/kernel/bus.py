"""
SOS Message Bus Service - The Nervous System.

Implements:
1. Redis Pub/Sub for Real-time Signal Transduction (Telepathy).
2. Redis Streams for Short-Term Memory (Hippocampus).
3. Distributed Tracing context propagation.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, List, AsyncIterator
from datetime import datetime

from sos.contracts.errors import MessageValidationError
from sos.kernel import Config, Message
from sos.observability.logging import get_logger


def enforce_scope(msg_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Raise if a Redis-field bus envelope is missing tenant_id/project.

    Phase 2 / W1. Kernel-level so both the MCP gateway and the service
    layer can reach it without crossing R2 (MCP must not import from
    sos.services.*). Pure dict inspection — no Redis contact, no
    Pydantic parse, no logging.

    Raises:
        MessageValidationError(SOS-4005) — tenant_id missing/empty.
        MessageValidationError(SOS-4006) — project missing/empty.
    """
    if not msg_dict.get("tenant_id"):
        raise MessageValidationError(
            "SOS-4005",
            "bus message missing required 'tenant_id' scope field",
            original_type=msg_dict.get("type"),
        )
    if not msg_dict.get("project"):
        raise MessageValidationError(
            "SOS-4006",
            "bus message missing required 'project' scope field",
            original_type=msg_dict.get("type"),
        )
    return msg_dict


# Lazy load redis to adhere to microkernel architecture
try:
    import redis.asyncio as redis
except ImportError:
    redis = None

log = get_logger("bus_service")

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(str(Path.home() / ".env.secrets"))


class MessageBus:
    """
    Central nervous system for Agent-to-Agent communication.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        from sos.kernel.settings import get_settings as _get_settings

        _s = _get_settings()
        # SOS_REDIS_URL takes precedence here to preserve legacy behaviour.
        self.redis_url = _s.redis.legacy_sos_url or "redis://localhost:6379/0"
        self.redis_password = _s.redis.password_str
        self._redis: Optional[redis.Redis] = None
        self._pubsub = None

        # Channel Patterns
        self.CHAN_PRIVATE = "sos:channel:private"
        self.CHAN_SQUAD = "sos:channel:squad"
        self.CHAN_GLOBAL = "sos:channel:global"

        # Memory Patterns
        self.MEM_PREFIX = "sos:memory:short"

    def _resolved_redis_url(self) -> str:
        """Prefer authenticated local Redis when only the default URL is configured."""
        if self.redis_password and self.redis_url == "redis://localhost:6379/0":
            return f"redis://:{self.redis_password}@localhost:6379/0"
        return self.redis_url

    async def connect(self):
        """Initialize Redis connection."""
        if not redis:
            log.warning("Redis library not installed. Bus is disabled.")
            return

        try:
            resolved_url = self._resolved_redis_url()
            self._redis = redis.from_url(resolved_url, decode_responses=True)
            await self._redis.ping()
            log.info(f"🔌 Connected to Nervous System (Redis) at {resolved_url}")
        except Exception as e:
            log.error(f"Failed to connect to Redis: {e}")
            self._redis = None

    async def disconnect(self):
        if self._redis:
            await self._redis.close()

    # --- TELEPATHY (Communication) ---

    async def send(
        self,
        message: Message,
        *,
        tenant_id: str,
        project: str,
        target_squad: Optional[str] = None,
    ):
        """Send a telepathic signal (Message).

        Scope is required (v0.9.1, Phase 2 W1). ``tenant_id`` is the hard
        customer boundary; ``project`` is the soft grouping inside a
        tenant. Both are stamped onto the published payload so every
        downstream stream entry carries its origin — no silent scope
        leaks.

        Routing (unchanged):
          - target_squad set → multicast to squad channel
          - message.target a specific agent → unicast to private inbox
          - otherwise → global broadcast
        """
        if not tenant_id:
            raise ValueError("MessageBus.send(): tenant_id is required")
        if not project:
            raise ValueError("MessageBus.send(): project is required")

        if not self._redis:
            return

        channel = self.CHAN_GLOBAL
        if target_squad:
            channel = f"{self.CHAN_SQUAD}:{target_squad}"
        elif message.target and message.target != "broadcast":
            # We want an inbox model: private:{recipient_id}.
            channel = f"{self.CHAN_PRIVATE}:{message.target}"

        # Stamp scope on the payload before it crosses the wire. The JSON
        # body already carries type/source/target/payload — we splice in
        # the scope fields so downstream consumers can audit every entry
        # against its tenant without re-deriving from channel names.
        body = json.loads(message.to_json())
        body["tenant_id"] = tenant_id
        body["project"] = project
        payload = json.dumps(body)

        await self._redis.publish(channel, payload)
        log.debug(f"Signal fired on {channel}: {message.type.value}")

        # Also store in Stream for durability/history
        await self._redis.xadd(f"sos:stream:{channel}", {"payload": payload}, maxlen=1000)

    async def subscribe(self, agent_id: str, squads: List[str]) -> AsyncIterator[Message]:
        """
        Connect an agent's brain to the nervous system.
        Subscribes to: Private Inbox + Squad Channels + Global.
        """
        if not self._redis:
            return

        ps = self._redis.pubsub()

        channels = [self.CHAN_GLOBAL, f"{self.CHAN_PRIVATE}:{agent_id}"]
        for squad in squads:
            channels.append(f"{self.CHAN_SQUAD}:{squad}")

        await ps.subscribe(*channels)
        log.info(f"Agent {agent_id} synapse connected to: {channels}")

        async for raw_msg in ps.listen():
            if raw_msg["type"] == "message":
                try:
                    data = raw_msg["data"]
                    # Deserialize
                    msg = Message.from_json(data)
                    yield msg
                except Exception as e:
                    log.error(f"Synapse misfire (deserialization error): {e}")

    # --- HIPPOCAMPUS (Short-Term Memory) ---

    async def memory_push(self, agent_id: str, content: str, role: str = "assistant"):
        """
        Push a thought/action to short-term working memory.
        """
        if not self._redis:
            return

        key = f"{self.MEM_PREFIX}:{agent_id}"
        entry = {"content": content, "role": role, "ts": datetime.utcnow().isoformat()}

        # Push to list (Left Push)
        await self._redis.lpush(key, json.dumps(entry))
        # Trim to last 50 items (Working Memory Window)
        await self._redis.ltrim(key, 0, 49)

    async def memory_recall(self, agent_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Recall recent working memory.
        """
        if not self._redis:
            return []

        key = f"{self.MEM_PREFIX}:{agent_id}"
        # LRANGE 0 N
        items = await self._redis.lrange(key, 0, limit - 1)

        memories = []
        for item in items:
            memories.append(json.loads(item))

        return memories


# Singleton
_bus = None


def get_bus() -> MessageBus:
    global _bus
    if _bus is None:
        _bus = MessageBus()
    return _bus
