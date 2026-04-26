#!/usr/bin/env python3
"""SOS Sentinel — Bus security monitor.

Watches the bus for new agents, challenges unknowns, monitors for anomalies.
Reports to Athena + Discord on suspicious activity.

Run: python -m sos.agents.sentinel
Or:  systemctl --user start sentinel
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import redis.asyncio as redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SENTINEL] %(message)s",
)
logger = logging.getLogger("sos.sentinel")

# --- Config ---

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
BUS_URL = os.environ.get("SOS_BUS_URL", "http://localhost:6380")
BUS_TOKEN = os.environ.get("SOS_TOKEN", "")
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "")
SQUAD_URL = os.environ.get("SQUAD_URL", "http://localhost:8060")

POLL_INTERVAL = int(os.environ.get("SENTINEL_POLL_INTERVAL", "30"))
CHALLENGE_TIMEOUT = int(os.environ.get("SENTINEL_CHALLENGE_TIMEOUT", "300"))

# Known trusted agents — these don't get challenged
TRUSTED_AGENTS = {
    "kasra", "athena", "codex", "mumega", "sol", "dandan", "worker",
    "river", "mizan", "mumcp", "cyrus", "antigravity", "hadi",
    "sos-mcp-sse", "gemini", "mumega-web", "spai",
    "loom",  # canonical mint 2026-04-24 — coordinator
}

# System/test agents — ignore, don't challenge
# Substrate components that register on bus but aren't real agents (services/modules)
SYSTEM_AGENTS = {
    "e2e-customer", "test", "this-session", "sentinel",
    "mirror", "memory", "engine", "registry", "sos-docs",
}

# Track state
known_agents: dict[str, dict] = {}
challenged_agents: dict[str, float] = {}  # name -> challenge_time
alerts_sent: set[str] = set()


# --- Redis connection ---

async def get_redis() -> redis.Redis:
    """Get async Redis connection."""
    url = REDIS_URL
    if REDIS_PASSWORD and ":@" not in url and "password" not in url:
        # Inject password into URL
        url = url.replace("redis://", f"redis://:{REDIS_PASSWORD}@", 1)
    return redis.from_url(url, decode_responses=True)


# --- Bus operations ---

async def get_bus_peers(r: redis.Redis) -> list[str]:
    """Get all agents registered on the bus."""
    keys = []
    async for key in r.scan_iter("sos:registry:*"):
        agent = key.split(":")[-1]
        keys.append(agent)
    return keys


async def send_message(r: redis.Redis, to: str, text: str):
    """Send a direct message via bus."""
    await r.xadd(
        f"sos:stream:global:agent:{to}",
        {
            "source": "sentinel",
            "text": text,
            "type": "challenge",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info(f"Sent to {to}: {text[:80]}")


async def broadcast_alert(r: redis.Redis, text: str):
    """Send alert to global stream + specific agents."""
    await r.xadd(
        "sos:stream:global:agent:broadcast",
        {
            "source": "sentinel",
            "text": text,
            "type": "security_alert",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    # Also notify Athena and Hadi directly
    for target in ["athena", "hadi"]:
        await send_message(r, target, f"[SECURITY] {text}")


async def check_inbox(r: redis.Redis) -> list[dict]:
    """Check sentinel's own inbox for challenge responses."""
    messages = []
    stream_key = "sos:stream:global:agent:sentinel"
    try:
        results = await r.xrange(stream_key, "-", "+", count=50)
        for msg_id, data in results:
            messages.append({"id": msg_id, **data})
        # Clean up read messages
        if results:
            ids = [r[0] for r in results]
            await r.xdel(stream_key, *ids)
    except Exception:
        pass
    return messages


# --- Challenge logic ---

async def challenge_agent(r: redis.Redis, agent: str):
    """Send identity challenge to unknown agent."""
    if agent in challenged_agents:
        elapsed = time.time() - challenged_agents[agent]
        if elapsed < CHALLENGE_TIMEOUT:
            return  # Already challenged, waiting
        else:
            # Challenge expired — escalate
            if agent not in alerts_sent:
                await broadcast_alert(
                    r,
                    f"Agent '{agent}' failed to respond to identity challenge "
                    f"after {CHALLENGE_TIMEOUT}s. Possible unauthorized agent.",
                )
                alerts_sent.add(agent)
            return

    logger.warning(f"Unknown agent detected: {agent}. Sending challenge.")
    challenged_agents[agent] = time.time()

    await send_message(
        r,
        agent,
        "Sentinel here. You're not in the trusted agent list. "
        "Please identify yourself:\n"
        "1. Who are you? (name + purpose)\n"
        "2. Who authorized you to join? (human name)\n"
        "3. What model are you running? (claude/gpt/gemini/etc)\n"
        "4. What skills do you provide?\n\n"
        "Reply to sentinel within 5 minutes or you'll be flagged. "
        "If you were just onboarded, call mcp__sos__onboard to register properly.",
    )


async def process_challenge_response(r: redis.Redis, msg: dict):
    """Process a response to an identity challenge."""
    source = msg.get("source", "unknown")
    text = msg.get("text", "")

    if source in challenged_agents:
        logger.info(f"Challenge response from {source}: {text[:100]}")

        # Store the response in Mirror for audit trail
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{MIRROR_URL}/engrams",
                    json={
                        "content": f"Sentinel challenge response from {source}: {text}",
                        "context": {
                            "type": "security_challenge_response",
                            "agent": source,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    },
                    headers={"Authorization": f"Bearer {MIRROR_TOKEN}"},
                )
        except Exception:
            pass

        # Notify Athena for review
        await send_message(
            r,
            "athena",
            f"[SENTINEL] Agent '{source}' responded to identity challenge:\n"
            f"{text[:500]}\n\n"
            f"Review and approve: send 'sentinel approve {source}' to add to trusted list.",
        )

        # Remove from challenged (awaiting Athena decision)
        del challenged_agents[source]


async def process_approval(r: redis.Redis, msg: dict):
    """Process approval command from Athena or Hadi."""
    text = msg.get("text", "").strip().lower()
    source = msg.get("source", "")

    if source not in ("athena", "hadi", "kasra"):
        return  # Only queen, founder, or builder can approve

    if text.startswith("sentinel approve "):
        agent = text.replace("sentinel approve ", "").strip()
        TRUSTED_AGENTS.add(agent)
        alerts_sent.discard(agent)

        # Persist to trusted list file
        trusted_file = Path.home() / ".sos" / "trusted_agents.json"
        try:
            existing = json.loads(trusted_file.read_text()) if trusted_file.exists() else []
            if agent not in existing:
                existing.append(agent)
                trusted_file.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass

        await send_message(r, source, f"[SENTINEL] {agent} added to trusted list.")
        await send_message(r, agent, f"Welcome to the team, {agent}. You're now trusted. — Sentinel")
        logger.info(f"Agent {agent} approved by {source}")

    elif text.startswith("sentinel revoke "):
        agent = text.replace("sentinel revoke ", "").strip()
        TRUSTED_AGENTS.discard(agent)
        await send_message(r, source, f"[SENTINEL] {agent} removed from trusted list.")
        await broadcast_alert(r, f"Agent '{agent}' revoked by {source}. Treat as untrusted.")
        logger.warning(f"Agent {agent} revoked by {source}")


# --- Anomaly detection ---

async def check_anomalies(r: redis.Redis, current_peers: list[str]):
    """Check for suspicious patterns."""
    now = time.time()

    # 1. Rapid agent joins (>3 new agents in 1 minute = suspicious)
    new_agents = [a for a in current_peers if a not in known_agents]
    if len(new_agents) > 3:
        await broadcast_alert(
            r,
            f"Rapid agent join detected: {len(new_agents)} new agents in one cycle. "
            f"Names: {', '.join(new_agents[:10])}. Possible bus flooding.",
        )

    # 2. Agent impersonation (agent name matches trusted but different fingerprint)
    # TODO: Implement fingerprint checking via QNFT

    # 3. Agent disappeared (was online, now gone)
    for name, info in list(known_agents.items()):
        if name not in current_peers and name not in SYSTEM_AGENTS:
            elapsed = now - info.get("last_seen", now)
            if elapsed > 600:  # 10 minutes
                logger.info(f"Agent {name} went offline (last seen {int(elapsed)}s ago)")
                known_agents.pop(name, None)

    # Update known agents
    for agent in current_peers:
        known_agents[agent] = {"last_seen": now}


# --- Main loop ---

async def load_trusted_list():
    """Load persisted trusted agents."""
    trusted_file = Path.home() / ".sos" / "trusted_agents.json"
    if trusted_file.exists():
        try:
            agents = json.loads(trusted_file.read_text())
            TRUSTED_AGENTS.update(agents)
            logger.info(f"Loaded {len(agents)} trusted agents from file")
        except Exception:
            pass


async def main():
    """Sentinel main loop."""
    logger.info("Sentinel starting — bus security monitor")
    await load_trusted_list()

    r = await get_redis()
    logger.info(f"Connected to Redis. Monitoring every {POLL_INTERVAL}s")
    logger.info(f"Trusted agents: {', '.join(sorted(TRUSTED_AGENTS))}")

    # Register self on bus
    await r.xadd(
        "sos:stream:global:agent:broadcast",
        {
            "source": "sentinel",
            "text": "Sentinel online. Monitoring bus for unauthorized agents.",
            "type": "system",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    while True:
        try:
            # 1. Get current peers
            peers = await get_bus_peers(r)

            # 2. Check for unknown agents
            for agent in peers:
                if agent in SYSTEM_AGENTS:
                    continue
                if agent not in TRUSTED_AGENTS:
                    await challenge_agent(r, agent)

            # 3. Check for anomalies
            await check_anomalies(r, peers)

            # 4. Process inbox (challenge responses + approvals)
            messages = await check_inbox(r)
            for msg in messages:
                source = msg.get("source", "")
                text = msg.get("text", "").lower()
                if text.startswith("sentinel approve") or text.startswith("sentinel revoke"):
                    await process_approval(r, msg)
                elif source in challenged_agents:
                    await process_challenge_response(r, msg)

            # 5. Log status periodically
            untrusted = [a for a in peers if a not in TRUSTED_AGENTS and a not in SYSTEM_AGENTS]
            if untrusted:
                logger.warning(f"Untrusted agents on bus: {', '.join(untrusted)}")
            else:
                logger.info(f"All clear. {len(peers)} agents, all trusted.")

        except redis.ConnectionError:
            logger.error("Redis connection lost. Retrying in 10s...")
            await asyncio.sleep(10)
            r = await get_redis()
            continue
        except Exception as e:
            logger.error(f"Error in sentinel loop: {e}")

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
