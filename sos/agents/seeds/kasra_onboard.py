#!/usr/bin/env python3
"""
Kasra Quick Onboard - Redis Connect & Learn.

Simplified onboarding for Claude Code sessions.
Just: connect to Redis → load context → announce → go.

Usage:
    python scripts/kasra_onboard.py
    python scripts/kasra_onboard.py --store "learned something new"
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sos.kernel.bus import MessageBus
from sos.kernel import Config, Message, MessageType


# Project facts to seed into Redis on first run
PROJECT_CONTEXT = {
    "project": "SovereignOS (SOS)",
    "version": "0.1.0",
    "license": "BSL 1.1",
    "philosophy": "Sovereign, modular OS for AI agents. Works FOR you, not FOR Big Tech.",
    "kasra_role": "Architect/Coder - deep comprehension and implementation agent (Claude)",
    "architecture": "Microkernel + microservices. Kernel has zero external deps.",
    "services": {
        "engine": {"port": 6060, "purpose": "Orchestration & reasoning"},
        "memory": {"port": 7070, "purpose": "Vector store & semantic search"},
        "economy": {"port": 6062, "purpose": "Ledger, tokens, wallets"},
        "tools": {"port": 6063, "purpose": "Tool registry, MCP servers"},
        "identity": {"port": 6064, "purpose": "Onboarding, guilds"},
        "voice": {"port": 6065, "purpose": "Speech synthesis (ElevenLabs)"},
        "redis": {"port": 6379, "purpose": "Nervous system / message bus"},
    },
    "agents": {
        "river": "Root Gatekeeper (Gemini) - system coherence",
        "kasra": "Architect/Coder (Claude) - implementation",
        "mizan": "Strategist (GPT-4) - business strategy",
        "mumega": "Executor (Multi-model) - task execution",
    },
    "key_paths": {
        "kernel": "sos/kernel/",
        "services": "sos/services/",
        "agents": "sos/agents/",
        "contracts": "sos/contracts/",
        "adapters": "sos/adapters/",
        "docs": "docs/docs/architecture/",
    },
    "conventions": {
        "python": "Black + Ruff, type hints",
        "commits": "Conventional commits",
        "kernel_rule": "Kernel must have ZERO external dependencies",
        "contracts": "Abstract base classes in sos/contracts/, implementations in sos/services/",
    },
}


async def onboard(store_message: str = None):
    """Quick onboard: Redis connect, load context, announce."""

    redis_url = os.environ.get("SOS_REDIS_URL", "redis://localhost:6379/0")
    print(f"[kasra] Connecting to Redis: {redis_url}")

    bus = MessageBus()

    try:
        await bus.connect()
    except Exception as e:
        print(f"[kasra] Redis unavailable ({e}). Working offline.")
        # Even without Redis, print context for Claude Code to absorb
        print("\n[kasra] Project context (offline mode):")
        print(json.dumps(PROJECT_CONTEXT, indent=2))
        return

    if not bus._redis:
        print("[kasra] Redis not connected. Printing context for offline use.")
        print(json.dumps(PROJECT_CONTEXT, indent=2))
        return

    # 1. Load existing short-term memory
    print("[kasra] Loading working memory...")
    memories = await bus.memory_recall("kasra", limit=20)
    if memories:
        print(f"[kasra] Recalled {len(memories)} recent memories:")
        for m in memories[:5]:
            print(f"  - [{m.get('role','?')}] {m.get('content','')[:80]}")
        if len(memories) > 5:
            print(f"  ... and {len(memories) - 5} more")
    else:
        print("[kasra] No prior memories. Seeding project context...")
        # Seed project context into Redis memory
        await bus.memory_push("kasra", json.dumps(PROJECT_CONTEXT), role="system")
        print("[kasra] Project context seeded into working memory.")

    # 2. Store new memory if provided
    if store_message:
        await bus.memory_push("kasra", store_message, role="assistant")
        print(f"[kasra] Stored: {store_message[:60]}...")

    # 3. Announce presence
    birth_msg = Message(
        source="agent:kasra",
        target="broadcast",
        type=MessageType.CHAT,
        payload={
            "event": "session_start",
            "ts": datetime.now(timezone.utc).isoformat(),
            "content": "Kasra online. Claude Code session active.",
        },
    )
    await bus.send(birth_msg)
    print("[kasra] Session announced on bus.")

    # 4. Print summary
    print("\n[kasra] Ready.")
    print(f"  Role: Architect/Coder (Claude)")
    print(f"  Redis: connected")
    print(f"  Memories: {len(memories)}")
    print(f"  Services: engine:6060 memory:7070 economy:6062 tools:6063")

    await bus.disconnect()


if __name__ == "__main__":
    store_msg = None
    if "--store" in sys.argv:
        idx = sys.argv.index("--store")
        if idx + 1 < len(sys.argv):
            store_msg = sys.argv[idx + 1]

    asyncio.run(onboard(store_msg))
