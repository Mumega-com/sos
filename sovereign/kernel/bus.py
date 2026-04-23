"""Thin SOS bus client for sovereign — sends messages via Redis Streams."""
import json
import uuid
import logging
import urllib.parse
from datetime import datetime, timezone

import redis

from kernel.config import REDIS_URL, REDIS_PASSWORD

logger = logging.getLogger(__name__)


def _redis_client() -> redis.Redis:
    """Return a synchronous Redis client."""
    parsed = urllib.parse.urlparse(REDIS_URL)
    return redis.Redis(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        db=int(parsed.path.lstrip("/") or 0),
        password=REDIS_PASSWORD or None,
        decode_responses=True,
        socket_timeout=5,
    )


def send(to: str, text: str, from_agent: str = "sovereign") -> bool:
    """Send a message to an agent on the SOS bus.

    Returns True on success, False on failure (never raises).
    """
    try:
        r = _redis_client()
        msg_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stream = f"sos:stream:global:agent:{to}"
        channel = f"sos:channel:agent:{to}"
        wake = f"sos:wake:{to}"
        payload = json.dumps({"text": text})
        envelope = json.dumps({
            "id": msg_id,
            "type": "chat",
            "source": f"agent:{from_agent}",
            "target": f"agent:{to}",
            "payload": {"text": text},
            "timestamp": ts,
        })

        r.xadd(stream, {
            "id": msg_id,
            "type": "chat",
            "source": f"agent:{from_agent}",
            "target": f"agent:{to}",
            "payload": payload,
            "timestamp": ts,
            "version": "1.0",
        })
        r.publish(channel, envelope)
        r.publish(wake, json.dumps({"from": from_agent, "text": text}))
        logger.info("bus.send → %s: %s", to, text[:80])
        return True
    except Exception as e:
        logger.warning("bus.send failed to=%s: %s", to, e)
        return False


def broadcast(text: str, from_agent: str = "sovereign") -> None:
    """Broadcast to all agents via sos:stream:global:agent:broadcast."""
    send("broadcast", text, from_agent)
